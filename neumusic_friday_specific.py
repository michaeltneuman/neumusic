import requests
from bs4 import BeautifulSoup
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime
from datetime import timedelta
import time
import re
import logging
from collections import defaultdict
from urllib.parse import quote
import os
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AlbumReleaseEmailer:
    def __init__(self, gmail_user, gmail_app_password, spotify_client_id, spotify_client_secret):
        self.gmail_user = gmail_user
        self.gmail_app_password = gmail_app_password
        
        # Initialize Spotify client
        client_credentials_manager = SpotifyClientCredentials(
            client_id=spotify_client_id,
            client_secret=spotify_client_secret
        )
        self.spotify = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        self.albums = {}  # {(artist, album): {sources: [], spotify_uri: '', description: ''}}
        self.errors = []
        
    def get_next_thursday_11_05_pm(self):
        """Calculate the next Thursday at 11:05 PM"""
        now = datetime.datetime.now()
        days_until_thursday = (3 - now.weekday()) % 7  # Thursday is 3
        if days_until_thursday == 0:  # Today is Thursday
            target_time = now.replace(hour=23, minute=5, second=0, microsecond=0)
            if now > target_time:  # Already passed 11:05 PM today
                days_until_thursday = 7
        
        if days_until_thursday == 0:
            next_thursday = target_time
        else:
            next_thursday = now + datetime.timedelta(days=days_until_thursday)
            next_thursday = next_thursday.replace(hour=23, minute=5, second=0, microsecond=0)
        
        return next_thursday
    
    def wait_until_thursday(self):
        """Sleep until next Thursday at 11:05 PM"""
        target_time = self.get_next_thursday_11_05_pm()
        now = datetime.datetime.now()
        
        if now < target_time:
            sleep_seconds = (target_time - now).total_seconds()
            logger.info(f"Waiting until {target_time} (sleeping for {sleep_seconds/3600:.2f} hours)")
            time.sleep(sleep_seconds)
    
    def get_tomorrow_date(self):
        """Get tomorrow's date in various formats"""
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        return {
            'date_obj': tomorrow,
            'month_name': tomorrow.strftime('%B'),
            'month_year': tomorrow.strftime('%B %Y'),
            'metacritic_format': tomorrow.strftime('%e %B %Y').strip(),
            'genius_format': tomorrow.strftime('||%m/||%d').replace('||0','||').replace('||',''),
            'wikipedia_format': tomorrow.strftime('%B||%d').replace('||0','||').replace('||',''),
            'spotify_format':tomorrow.strftime('%Y-%m-%d'),
            'iso_format': tomorrow.isoformat(),
            'day': tomorrow.day,
            'month': tomorrow.month,
            'year': tomorrow.year
        }
    
    def scrape_metacritic(self, target_date):
        """Scrape Metacritic for album releases"""
        try:
            url = "https://www.metacritic.com/browse/albums/release-date/coming-soon/date"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            album_items = soup.find('table',{'class':lambda _:_ and 'musicTable' in _}).find_all('tr')            
            found_albums = []
            date_active = False
            for item in album_items:
                try:
                    try:
                        if item.find('th').text.strip() == target_date['metacritic_format']:
                            logging.warning("Date active now!")
                            date_active = True
                        elif item.find('th').text.strip() == (target_date['date_obj']+timedelta(days=7)).strftime('%e %B %Y'):
                            logging.warning("Date no longer active!")
                            break
                    except :
                        pass
                    if date_active:
                        artist = item.find('td',{'class':lambda _:_ and 'artistName' in _}).text.strip()
                        album = item.find('td',{'class':lambda _:_ and 'albumTitle' in _}).text.strip()
                        found_albums.append((artist, album))
                        logging.info(f"{artist} | {album}")
                except Exception as e:
                    logging.warning(f"{e}")
            if not found_albums:
                self.errors.append("Metacritic: No albums found or site structure may have changed")
                exit()
            return found_albums
        except Exception as e:
            error_msg = f"Error scraping Metacritic: {e}"
            logger.error(error_msg)
            self.errors.append(error_msg)
            exit()
            return []
    
    def scrape_genius(self, target_date):
        """Scrape Genius for album releases"""
        try:
            month_year = target_date['month_year'].lower().replace(' ', '-')
            url = f"https://genius.com/Genius-{month_year}-album-release-calendar-annotated"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            album_items = soup.find_all(['b','a'])
            found_albums = []
            date_active = False
            for item in tqdm(album_items):
                try:
                    if item.name == 'b':
                        if item.text.strip() == target_date['genius_format']:
                            logging.warning("Date active now!")
                            date_active = True
                        elif item.text.strip() == (target_date['date_obj']+timedelta(days=7)).strftime('||%m/||%d').replace('||0','||').replace('||',''):
                            logging.warning("Date no longer active!")
                            break
                    elif date_active:
                        album_info = item.text.strip().split(' - ')[:-1]
                        artist = album_info[0]
                        album = ' - '.join(album_info[1:])
                        found_albums.append((artist, album))
                        logging.info(f"{artist} | {album}")
                except Exception as ee:
                    print(f"{ee}")
                    continue
            if not found_albums:
                self.errors.append("Genius: No albums found or site structure may have changed")
                exit()
            return found_albums
            
        except Exception as e:
            error_msg = f"Error scraping Genius: {e}"
            logger.error(error_msg)
            self.errors.append(error_msg)
            exit()
            return []
    
    def scrape_wikipedia(self, target_date):
        """Scrape Wikipedia for album releases"""
        try:
            month_year = target_date['month_year']
            year = target_date['year']
            url = f"https://en.m.wikipedia.org/wiki/List_of_{year}_albums"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            album_items_tables = soup.find_all('table')
            found_albums = []
            for table in album_items_tables:
                try:
                    if table.find('caption').text.strip().endswith(month_year):
                        date_active = False
                        for album_item in table.find_all('tr'):
                            try:
                                if album_item.find('th').text.strip() == target_date['wikipedia_format']:
                                    logging.warning("Date active now!")
                                    date_active = True
                                elif album_item.find('th').text.strip() == (target_date['date_obj']+timedelta(days=7)).strftime('%B||%d').replace('||0','||').replace('||',''):
                                    logging.warning("Date no longer active!")
                                    break
                            except:
                                pass
                            if date_active:
                                album_item = album_item.find_all('td')
                                artist = album_item[0].text.strip()
                                album = album_item[1].text.strip()
                                found_albums.append((artist, album))
                                logging.info(f"{artist} | {album}")
                except Exception as e:
                    logging.warning(f"{e}")
            if not found_albums:
                self.errors.append("Wikipedia: No albums found or site structure may have changed")
                exit()
            return found_albums
        except Exception as e:
            error_msg = f"Error scraping Wikipedia: {e}"
            logger.error(error_msg)
            self.errors.append(error_msg)
            exit()
            return []
    
    def search_spotify_album(self, artist, album,target_date):
        """Search for album on Spotify and return URI"""
        try:
            # Try exact search first
            query = f"artist:{artist} album:{album}"
            results = self.spotify.search(q=query, type='album', limit=50)
            
            albums = results['albums']['items']
            logging.info(f"{artist} | {album} | QUERY 1 {len(albums)}")
            # If no exact match, try broader search
            if not albums:
                query = f"{artist} {album}"
                results = self.spotify.search(q=query, type='album', limit=50)
                albums = results['albums']['items']
                logging.info(f"{artist} | {album} | QUERY 2 {len(albums)}")
            if albums:
                best_albums = []
                for album in albums:
                    if album['release_date'] in (target_date['spotify_format'],(target_date['date_obj']+timedelta(days=-1)).strftime('%Y-%m-%d')):
                        logging.info(f"BEST ALBUM FOUND {album}")
                        best_albums.append(album)
                if best_albums:
                    best_album = max(best_albums, key=lambda x: x['total_tracks'])
                    logging.info(f"BEST ALBUM CHOSEN {best_album}")
                    return {
                                'uri': best_album['uri'],
                                'tracks': best_album['total_tracks'],
                                'image_url': best_album['images'][0]['url'] if best_album['images'] else None,
                                'spotify_url': best_album['external_urls']['spotify']
                            }
            return None
            
        except Exception as e:
            logger.error(f"Error searching Spotify for {artist} - {album}: {e}")
            return None
    
    def get_artist_info(self, artist):
        """Get artist information from various sources"""
        try:
            # Search Spotify for artist info
            results = self.spotify.search(q=f"artist:{artist}", type='artist', limit=1)
            
            if results['artists']['items']:
                spotify_artist = results['artists']['items'][0]
                genres = spotify_artist.get('genres', [])
                followers = spotify_artist.get('followers', {}).get('total', 0)
                
                # Get top tracks for popular works
                top_tracks = self.spotify.artist_top_tracks(spotify_artist['id'])['tracks']
                popular_works = [track['name'] for track in top_tracks[:3]]
                
                return {
                    'genres': genres[:3] if genres else ['Unknown'],  # Limit to top 3 genres
                    'followers': followers,
                    'popular_works': popular_works,
                    'spotify_url': spotify_artist['external_urls']['spotify']
                }
            
            return {'genres': ['Unknown'], 'followers': 0, 'popular_works': [], 'spotify_url': ''}
            
        except Exception as e:
            logger.error(f"Error getting artist info for {artist}: {e}")
            return {'genres': ['Unknown'], 'followers': 0, 'popular_works': [], 'spotify_url': ''}
    
    def deduplicate_albums(self):
        """Deduplicate albums, keeping the one with more Spotify tracks if available"""
        seen_combinations = {}
        
        for (artist, album), data in list(self.albums.items()):
            # Create a normalized key for comparison
            key = (artist.lower().strip(), album.lower().strip())
            
            if key in seen_combinations:
                # Compare Spotify track counts
                existing_tracks = seen_combinations[key]['spotify_info'].get('tracks', 0) if seen_combinations[key]['spotify_info'] else 0
                current_tracks = data['spotify_info'].get('tracks', 0) if data['spotify_info'] else 0
                
                if current_tracks > existing_tracks:
                    # Remove old entry and keep current
                    old_key = seen_combinations[key]['original_key']
                    del self.albums[old_key]
                    seen_combinations[key] = {'original_key': (artist, album), 'spotify_info': data['spotify_info']}
                else:
                    # Remove current entry
                    del self.albums[(artist, album)]
            else:
                seen_combinations[key] = {'original_key': (artist, album), 'spotify_info': data['spotify_info']}
    
    def generate_html_email(self, target_date):
        """Generate HTML email content"""
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Album Releases for {target_date['iso_format']}</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: #f8f9fa;
                }}
                .container {{
                    background-color: white;
                    border-radius: 10px;
                    padding: 30px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 30px;
                    padding-bottom: 20px;
                    border-bottom: 3px solid #1db954;
                }}
                .header h1 {{
                    color: #1db954;
                    margin: 0;
                    font-size: 2.5em;
                }}
                .date {{
                    color: #666;
                    font-size: 1.2em;
                    margin: 10px 0;
                }}
                .error-banner {{
                    background-color: #ff4757;
                    color: white;
                    padding: 15px;
                    border-radius: 5px;
                    margin-bottom: 20px;
                    font-weight: bold;
                    text-align: center;
                    font-size: 1.1em;
                }}
                .album-card {{
                    border: 1px solid #ddd;
                    border-radius: 8px;
                    margin-bottom: 20px;
                    overflow: hidden;
                    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
                    background: white;
                }}
                .album-header {{
                    background: linear-gradient(135deg, #1db954, #1ed760);
                    color: white;
                    padding: 15px 20px;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }}
                .album-title {{
                    font-size: 1.3em;
                    font-weight: bold;
                    margin: 0;
                }}
                .artist-name {{
                    font-size: 1.1em;
                    opacity: 0.9;
                    margin: 5px 0 0 0;
                }}
                .album-body {{
                    padding: 20px;
                    display: flex;
                    gap: 20px;
                }}
                .album-image {{
                    flex-shrink: 0;
                }}
                .album-image img {{
                    width: 120px;
                    height: 120px;
                    object-fit: cover;
                    border-radius: 8px;
                    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
                }}
                .album-info {{
                    flex: 1;
                }}
                .info-section {{
                    margin-bottom: 15px;
                }}
                .info-label {{
                    font-weight: bold;
                    color: #1db954;
                    margin-bottom: 5px;
                }}
                .genres {{
                    display: flex;
                    gap: 8px;
                    flex-wrap: wrap;
                }}
                .genre-tag {{
                    background-color: #e8f5e8;
                    color: #1db954;
                    padding: 4px 8px;
                    border-radius: 12px;
                    font-size: 0.9em;
                }}
                .popular-works {{
                    list-style: none;
                    padding: 0;
                    margin: 0;
                }}
                .popular-works li {{
                    background-color: #f8f9fa;
                    padding: 5px 10px;
                    margin: 3px 0;
                    border-radius: 4px;
                    font-size: 0.9em;
                }}
                .spotify-links {{
                    display: flex;
                    gap: 10px;
                    margin-top: 15px;
                }}
                .spotify-link {{
                    background-color: #1db954;
                    color: white;
                    text-decoration: none;
                    padding: 8px 16px;
                    border-radius: 20px;
                    font-size: 0.9em;
                    font-weight: bold;
                    transition: background-color 0.3s;
                }}
                .spotify-link:hover {{
                    background-color: #1aa34a;
                    color: white;
                    text-decoration: none;
                }}
                .warning {{
                    background-color: #ffa502;
                    color: white;
                    padding: 10px 15px;
                    border-radius: 5px;
                    margin-top: 10px;
                }}
                .debug-info {{
                    background-color: #f1f2f6;
                    padding: 10px;
                    border-radius: 4px;
                    font-family: monospace;
                    font-size: 0.8em;
                    margin-top: 5px;
                }}
                .sources {{
                    color: #666;
                    font-size: 0.9em;
                    margin-top: 10px;
                }}
                .footer {{
                    text-align: center;
                    margin-top: 40px;
                    padding-top: 20px;
                    border-top: 1px solid #ddd;
                    color: #666;
                }}
                .stats {{
                    background-color: #f8f9fa;
                    padding: 15px;
                    border-radius: 8px;
                    margin-bottom: 20px;
                    text-align: center;
                }}
                .no-albums {{
                    text-align: center;
                    padding: 40px;
                    color: #666;
                    font-size: 1.2em;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎵 New Album Releases</h1>
                    <div class="date">Coming out on {target_date['date_obj'].strftime('%A, %B %d, %Y')}</div>
                </div>
        """
        
        # Add error banner if there are errors
        if self.errors:
            html_content += '<div class="error-banner">⚠️ WEBSITE STRUCTURE ISSUES DETECTED ⚠️<br>'
            for error in self.errors:
                html_content += f"{error}<br>"
            html_content += "</div>"
        
        # Add stats
        album_count = len(self.albums)
        html_content += f"""
        <div class="stats">
            <strong>📊 Found {album_count} album{'s' if album_count != 1 else ''} releasing tomorrow</strong>
        </div>
        """
        
        if not self.albums:
            html_content += """
            <div class="no-albums">
                <h2>🎭 No albums found for tomorrow</h2>
                <p>Either no albums are being released, or the websites may have changed their structure.</p>
            </div>
            """
        else:
            # Add albums
            for (artist, album), data in self.albums.items():
                html_content += f"""
                <div class="album-card">
                    <div class="album-header">
                        <div>
                            <div class="album-title">{album}</div>
                            <div class="artist-name">by {artist}</div>
                        </div>
                    </div>
                    <div class="album-body">
                """
                
                # Add album image if available
                if data['spotify_info'] and data['spotify_info'].get('image_url'):
                    html_content += f"""
                    <div class="album-image">
                        <img src="{data['spotify_info']['image_url']}" alt="{album} cover">
                    </div>
                    """
                
                html_content += f"""
                        <div class="album-info">
                            <div class="info-section">
                                <div class="info-label">Genres:</div>
                                <div class="genres">
                """
                
                for genre in data['artist_info']['genres']:
                    html_content += f'<span class="genre-tag">{genre}</span>'
                
                html_content += """
                                </div>
                            </div>
                """
                
                if data['artist_info']['popular_works']:
                    html_content += """
                            <div class="info-section">
                                <div class="info-label">Popular Tracks:</div>
                                <ul class="popular-works">
                    """
                    for track in data['artist_info']['popular_works']:
                        html_content += f"<li>{track}</li>"
                    html_content += "</ul></div>"
                
                # Add Spotify links
                spotify_links = []
                if data['spotify_info']:
                    spotify_links.append(f'<a href="{data["spotify_info"]["spotify_url"]}" class="spotify-link">🎵 Album</a>')
                if data['artist_info']['spotify_url']:
                    spotify_links.append(f'<a href="{data["artist_info"]["spotify_url"]}" class="spotify-link">👤 Artist</a>')
                
                if spotify_links:
                    html_content += f"""
                            <div class="spotify-links">
                                {' '.join(spotify_links)}
                            </div>
                    """
                
                # Add warning if no Spotify album found
                if not data['spotify_info']:
                    html_content += f"""
                            <div class="warning">
                                ⚠️ NO SPOTIFY ALBUM FOUND
                                <div class="debug-info">
                                    Searched for: artist:{artist} album:{album}<br>
                                    Also tried: {artist} {album}
                                </div>
                            </div>
                    """
                
                # Add sources
                sources_text = ", ".join(data['sources'])
                html_content += f"""
                            <div class="sources">
                                📍 Found on: {sources_text}
                            </div>
                        </div>
                    </div>
                </div>
                """
        
        html_content += f"""
                <div class="footer">
                    <p>Generated on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                    <p>🎧 Happy listening! 🎧</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html_content
    
    def send_email(self, html_content, target_date):
        """Send the HTML email"""
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"🎵 Album Releases for {target_date['date_obj'].strftime('%B %d, %Y')}"
            msg['From'] = self.gmail_user
            msg['To'] = self.gmail_user
            
            html_part = MIMEText(html_content, 'html')
            msg.attach(html_part)
            
            server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
            server.login(self.gmail_user, self.gmail_app_password)
            text = msg.as_string()
            server.sendmail(self.gmail_user, self.gmail_user, text)
            server.quit()
            
            logger.info("Email sent successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
    
    def collect_albums(self):
        """Collect albums from all sources"""
        target_date = self.get_tomorrow_date()
        logger.info(f"Looking for albums releasing on {target_date['iso_format']}")
        
        # Clear previous data
        self.albums.clear()
        self.errors.clear()
        
        # Scrape all sources
        sources_data = [
            ('Metacritic', self.scrape_metacritic(target_date)),
            ('Genius', self.scrape_genius(target_date)),
            ('Wikipedia', self.scrape_wikipedia(target_date))
        ]
        
        # Process albums from all sources
        for source_name, albums_list in sources_data:
            for artist, album in albums_list:
                key = (artist, album)
                if key not in self.albums:
                    self.albums[key] = {
                        'sources': [],
                        'spotify_info': None,
                        'artist_info': None
                    }
                self.albums[key]['sources'].append(source_name)
        
        self.deduplicate_albums()
        
        # Get Spotify and artist info for each album
        for (artist, album), data in self.albums.items():
            logger.info(f"Processing: {artist} - {album}")
            data['spotify_info'] = self.search_spotify_album(artist, album,target_date)
            data['artist_info'] = self.get_artist_info(artist)
            time.sleep(1)
        logger.info(f"Found {len(self.albums)} unique albums")
    
    def run_once(self):
        """Run the email generation and sending process once"""
        target_date = self.get_tomorrow_date()
        
        # Collect albums
        self.collect_albums()
        
        # Generate and send email
        html_content = self.generate_html_email(target_date)
        success = self.send_email(html_content, target_date)
        
        return success
    
    def run_continuous(self):
        """Run continuously, checking every Thursday at 11:05 PM"""
        logger.info("Starting continuous album release emailer...")
        
        while True:
            try:
                # Wait until next Thursday 11:05 PM
                self.wait_until_thursday()
                
                # Run the process
                logger.info("Running album release check...")
                success = self.run_once()
                
                if success:
                    logger.info("Successfully sent album release email")
                else:
                    logger.error("Failed to send album release email")
                
                # Sleep for a minute to avoid running multiple times
                time.sleep(60)
                
            except KeyboardInterrupt:
                logger.info("Shutting down album release emailer...")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(300)  # Sleep 5 minutes on error

def main():
    emailer = AlbumReleaseEmailer(
        gmail_user=os.getenv('GMAIL_USER'),
        gmail_app_password=os.getenv('GMAIL_PASSWORD'),
        spotify_client_id=os.getenv('SPOTIPY_001_CLIENT_ID'),
        spotify_client_secret=os.getenv('SPOTIPY_001_CLIENT_SECRET'),
    )
    emailer.run_continuous()

if __name__ == "__main__":
    main()
