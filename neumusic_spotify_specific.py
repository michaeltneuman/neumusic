import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
import logging
from tqdm import tqdm

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SpotifyReleaseMonitor:
    def __init__(self, client_id, client_secret, redirect_uri, gmail_email, gmail_password):
        # Spotify setup
        self.scope = "user-read-recently-played user-top-read playlist-read-private"
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=self.scope
        ))
        
        # Email setup
        self.gmail_email = gmail_email[0]
        self.gmail_password = gmail_password
        
        # Data storage file
        self.data_file = "spotify_monitor_data.json"
        
        # Load or initialize data
        self.load_data()
        
    def load_data(self):
        """Load existing data or create new data structure"""
        try:
            with open(self.data_file, 'r') as f:
                self.data = json.load(f)
            logger.info("Loaded existing data from file")
        except FileNotFoundError:
            self.data = {
                "artists": {},  # artist_id: {"name": str, "last_check": str}
                "known_releases": {},  # release_id: {"artist_id": str, "name": str, "type": str, "date": str, "url": str}
                "last_artist_update": None
            }
            logger.info("Created new data structure")
    
    def save_data(self):
        """Save data to file"""
        with open(self.data_file, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def get_artists_from_playlist(self, playlist_name):
        """Get all artists from a specific playlist"""
        logger.info(f"Getting artists from playlist: {playlist_name}")
        artists = set()
        
        # Get user's playlists
        playlists = self.sp.current_user_playlists()
        time.sleep(5)  # Rate limit pause
        
        target_playlist = None
        while playlists:
            for playlist in tqdm(playlists['items']):
                if playlist['name'] == playlist_name:
                    target_playlist = playlist
                    break
            
            if target_playlist or not playlists['next']:
                break
            
            playlists = self.sp.next(playlists)
            time.sleep(5)  # Rate limit pause
        
        if not target_playlist:
            logger.warning(f"Playlist '{playlist_name}' not found")
            return artists
        
        # Get tracks from playlist
        tracks = self.sp.playlist_tracks(target_playlist['id'])
        time.sleep(5)  # Rate limit pause
        
        while tracks:
            for item in tqdm(tracks['items']):
                if item['track'] and item['track']['artists']:
                    for artist in item['track']['artists']:
                        artists.add((artist['id'], artist['name']))
            
            if not tracks['next']:
                break
            
            tracks = self.sp.next(tracks)
            time.sleep(5)  # Rate limit pause
        
        logger.info(f"Found {len(artists)} unique artists in playlist")
        return artists
    
    def get_top_artists(self):
        """Get top artists from all time ranges"""
        logger.info("Getting top artists from all time ranges")
        artists = set()
        
        time_ranges = ['short_term', 'medium_term', 'long_term']
        
        for time_range in tqdm(time_ranges):
            logger.info(f"Getting {time_range} top artists")
            top_artists = self.sp.current_user_top_artists(limit=50, time_range=time_range)
            time.sleep(5)  # Rate limit pause
            all_artists = top_artists['items']
            while top_artists['next']:
                top_artists = self.sp.next(top_artists)
                time.sleep(5)  # Rate limit pause
                all_artists.extend(top_artists['items'])
            for artist in tqdm(all_artists):
                artists.add((artist['id'], artist['name']))
            logger.info(f"Getting {time_range} top tracks")
            top_tracks = self.sp.current_user_top_tracks(limit=50, time_range=time_range)
            time.sleep(5)  # Rate limit pause
            all_tracks = top_tracks['items']
            while top_tracks['next']:
                top_tracks = self.sp.next(top_tracks)
                time.sleep(5)  # Rate limit pause
                all_tracks.extend(top_tracks['items'])
            for track in tqdm(all_tracks):
                for artist in track['artists']:
                    artists.add((artist['id'], artist['name']))
        logger.info(f"Found {len(artists)} unique top artists")
        return artists
    
    def update_artist_list(self):
        """Update the list of artists to monitor"""
        logger.info("Updating artist list...")
        
        # Get artists from top artists and starred playlist
        top_artists = self.get_top_artists()
        playlist_artists = self.get_artists_from_playlist("Starred")
        
        # Combine all artists
        all_artists = top_artists.union(playlist_artists)
        
        # Update data structure
        current_time = datetime.now(timezone.utc).isoformat()
        for artist_id, artist_name in tqdm(all_artists):
            if artist_id not in self.data["artists"]:
                self.data["artists"][artist_id] = {
                    "name": artist_name,
                    "last_check": None
                }
        
        self.data["last_artist_update"] = current_time
        self.save_data()
        
        logger.info(f"Updated artist list with {len(all_artists)} artists")
    
    def get_artist_releases(self, artist_id, since_date=None):
        """Get releases for a specific artist since a given date"""
        releases = []
        
        try:
            # Get albums
            albums = self.sp.artist_albums(artist_id, album_type='album,single,compilation', limit=50)
            time.sleep(5)  # Rate limit pause
            all_albums = albums['items']
            while albums['next']:
                albums = self.sp.next(albums)
                time.sleep(5)  # Rate limit pause
                all_albums.extend(albums['items'])
            for album in all_albums:
                if album['release_date_precision']!='day':
                    continue
                release_date = album['release_date']
                release_datetime = datetime.strptime(release_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                # Check if this is a new release
                if since_date is None or release_datetime > since_date:
                    releases.append({
                        'id': album['id'],
                        'name': album['name'],
                        'type': album['album_type'],
                        'release_date': release_date,
                        'url': album['external_urls']['spotify']
                    })
        
        except Exception as e:
            logger.error(f"Error getting releases for artist {artist_id}: {e}")
        
        return releases
    
    def send_email_notification(self, releases_dict):
        msg = MIMEMultipart()
        msg['From'] = self.gmail_email
        msg['To'] = self.gmail_email
        msg['Subject'] = "🎵 New Releases Alert 🎵"
        body = """<html><body>"""
        for artist_id,release_info in releases_dict.items():
            body += f"""<p><h2>{release_info['artist_name']}</h2>"""
            for release in release_info['releases']:
                body += f"""<strong><a href="{release['url']}">{release['name']}</a></strong>"""
            body += """</p>"""
        body += """</body></html>"""
        msg.attach(MIMEText(body, 'html'))
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(self.gmail_email, self.gmail_password)
        server.sendmail(self.gmail_email, self.gmail_email, msg.as_string())
        server.quit()
    
    def check_for_new_releases(self):
        """Check all monitored artists for new releases"""
        logger.info("Checking for new releases...")
        current_time = datetime.now(timezone.utc)
        sorted_artists = sorted(
            self.data["artists"].items(),
            key=lambda x: datetime.fromisoformat(x[1]['last_check'].replace('Z', '+00:00')) if x[1]['last_check'] else datetime.min.replace(tzinfo=timezone.utc)
        )
        releases_to_send_emails_for = {}
        for artist_id, artist_info in tqdm(sorted_artists):
            logger.info(f"Checking releases for {artist_info['name']}")
            
            # Determine since when to check
            since_date = None
            if artist_info['last_check']:
                since_date = datetime.fromisoformat(artist_info['last_check'].replace('Z', '+00:00'))
            
            # Get releases
            releases = self.get_artist_releases(artist_id, since_date)
            
            # Process new releases
            for release in releases:
                release_key = f"{artist_id}_{release['id']}"
                
                # Check if we've already notified about this release
                if release_key not in self.data["known_releases"] and release['release_date'] in (datetime.now().strftime('%Y-%m-%d'),(datetime.now()+timedelta(days=-1)).strftime('%Y-%m-%d')):
                    logger.info(f"New release found: {artist_info['name']} - {release['name']}")
                    if artist_id not in releases_to_send_emails_for:
                        releases_to_send_emails_for[artist_id] = {'artist_name':artist_info['name'],'releases':list()}
                    releases_to_send_emails_for[artist_id]['releases'].append(release)
                    
                    # Record this release
                    self.data["known_releases"][release_key] = {
                        "artist_id": artist_id,
                        "artist_name": artist_info['name'],
                        "name": release['name'],
                        "type": release['type'],
                        "release_date": release['release_date'],
                        "url": release['url']
                    }
            # Update last check time
            self.data["artists"][artist_id]['last_check'] = current_time.isoformat()
            
            # Save after each artist to avoid losing data
            self.save_data()
        if len(releases_to_send_emails_for)>0:
            self.send_email_notification(releases_to_send_emails_for)
        logger.info("Finished checking for new releases")
    
    def run_initial_scan(self):
        """Run initial scan to populate known releases"""
        logger.info("Running initial scan to populate known releases...")
        
        for artist_id, artist_info in tqdm(self.data["artists"].items()):
            logger.info(f"Initial scan for {artist_info['name']}")
            
            # Get all releases (no date filter)
            releases = self.get_artist_releases(artist_id)
            
            # Add all existing releases to known releases
            for release in releases:
                release_key = f"{artist_id}_{release['id']}"
                self.data["known_releases"][release_key] = {
                    "artist_id": artist_id,
                    "artist_name": artist_info['name'],
                    "name": release['name'],
                    "type": release['type'],
                    "release_date": release['release_date'],
                    "url": release['url']
                }
            
            # Set initial check time
            self.data["artists"][artist_id]['last_check'] = datetime.now(timezone.utc).isoformat()
        
        self.save_data()
        logger.info("Initial scan completed")
    
    def run_monitor(self):
        """Main monitoring loop"""
        logger.info("Starting Spotify Release Monitor")
        
        
        # Check if this is the first run
        if not any(artist['last_check'] for artist in self.data["artists"].values()):
            logger.info("First run detected - performing initial scan")
            self.run_initial_scan()
        
        while True:
            for loop_num in range(10):
                if loop_num % 10 == 0:
                    self.update_artist_list()
                try:
                    self.check_for_new_releases()
                except KeyboardInterrupt:
                    logger.info("Monitor stopped by user")
                    break
                except Exception as e:
                    logger.error(f"Error in monitoring loop: {e}")
                    logger.info("Sleeping for 10 minutes before retry...")
                    time.sleep(600)

if __name__ == "__main__":
    SPOTIFY_CLIENT_ID = input("SPOTIFY CLIENT_ID?\n")
    SPOTIFY_CLIENT_SECRET = input("SPOTIFY CLIENT_SECRET?\n")
    SPOTIFY_REDIRECT_URI = "http://localhost/callback"  # Or your registered redirect URI
    GMAIL_EMAIL = input("GMAIL USERNAME?\n"),
    GMAIL_APP_PASSWORD = input("GMAIL PASSWORD?\n")
    monitor = SpotifyReleaseMonitor(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        gmail_email=GMAIL_EMAIL,
        gmail_password=GMAIL_APP_PASSWORD
    )
    

    monitor.run_monitor()

