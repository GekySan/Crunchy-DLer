# -*- coding: utf-8 -*-

import base64
import html
import re
import subprocess
import os
from typing import List, Dict, Optional, Tuple, Any
import uuid
import xml.etree.ElementTree as ET

from curl_cffi import requests
import customtkinter
from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH
from rich import print
import tkinter as tk
from tqdm import tqdm

DEVICE_WVD_FILE = "./Device.wvd"
OUTPUT_DIR_BASE = "output"

CRUNCHYROLL_DOMAIN            = "crunchyroll.com"
REGEX_URL                     = rf"https:\/\/www\.{CRUNCHYROLL_DOMAIN}\/fr\/watch\/([a-zA-Z0-9]+)\/"
CRUNCHYROLL_API_BASE_URL      = f"https://beta-api.{CRUNCHYROLL_DOMAIN}"
CRUNCHYROLL_CMS_BASE_URL      = f"https://www.{CRUNCHYROLL_DOMAIN}/content/v2/cms"
CRUNCHYROLL_PLAYBACK_BASE_URL = f"https://www.{CRUNCHYROLL_DOMAIN}/playback/v2"
CRUNCHYROLL_LICENSE_URL       = f"https://www.{CRUNCHYROLL_DOMAIN}/license/v1/license/widevine"
CRUNCHYROLL_STATIC_BASE_URL   = f"https://static.{CRUNCHYROLL_DOMAIN}/skip-events/production"

AUTH_HEADERS = {
    "Authorization": "Basic YXJ1ZDEtbnJhdGcxYW94NmRsaGU6TlRDMXFpdGczQ3p1TWVkTnlZZ3BGblk0NzdVTGxacnk=",
    "User-Agent": "Crunchyroll/4.48.1 (bundle_identifier:com.crunchyroll.iphone; build_number:3578348.327156123) iOS/17.4.1 Gravity/4.48.1"
}
FIREFOX_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr,fr-FR;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "x-cr-tab-id": "850dca82-5090-48b8-8761-4356c97205e4"
}
ANDROID_HEADERS = {
    "Authorization": "",
    "User-Agent": "Crunchyroll/3.32.2 Android/7.1.2 okhttp/4.9.2"
}


def parse_lr(text, left, right, recursive, unescape=True):
    """
    Parse the text for substrings between 'left' and 'right' markers.

    Parameters:
    text (str): The text to search within.
    left (str): The left marker.
    right (str): The right marker.
    recursive (bool): If True, returns all matches, else returns the first match.
    unescape (bool): If True, unescapes HTML entities in the found text.

    Returns:
    list or str: All matches if recursive is True, else the first match.
    """
    pattern = re.escape(left) + '(.*?)' + re.escape(right)
    matches = re.findall(pattern, text)

    if unescape:
        matches = [html.unescape(match) for match in matches]

    return matches if recursive else matches[0] if matches else None

def parse_mpd_content(mpd_content):
    if not mpd_content.strip():
        print("Le contenu MPD est vide.")
        return None
    try:
        tree = ET.ElementTree(ET.fromstring(mpd_content))
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"Erreur de parsing XML : {e}")
        return None

    def add_namespace(tag):
        return f'{{{root.tag.split("}")[0][1:]}}}{tag}'

    def calculate_num_fragments(timeline):
        total = 0
        for s in timeline.findall(add_namespace('S')):
            r = s.get('r')
            if r is not None:
                try:
                    total += int(r) + 1
                except ValueError:
                    print(f"Valeur de 'r' invalide : {r}")
            else:
                total += 1
        return total

    video_choices = []
    audio_choices = []

    for period in root.findall(add_namespace('Period')):
        for adaptation in period.findall(add_namespace('AdaptationSet')):
            for representation in adaptation.findall(add_namespace('Representation')):
                base_url_elem = representation.find(add_namespace('BaseURL'))
                base_url = base_url_elem.text if base_url_elem is not None else ""
                if not base_url:
                    print("BaseURL manquant pour une représentation.")
                    continue

                seg_template = adaptation.find(add_namespace('SegmentTemplate'))
                media_template = None
                fragment_count = 0
                if seg_template is not None:
                    media_template = seg_template.attrib.get('media')
                    init_template = seg_template.attrib.get('initialization')
                    timeline = seg_template.find(add_namespace('SegmentTimeline'))
                    if timeline is not None:
                        fragment_count = calculate_num_fragments(timeline)

                info = representation.attrib
                if info.get('mimeType') == 'video/mp4':
                    try:
                        w = int(info.get('width', 0))
                        h = int(info.get('height', 0))
                        res = f"{w}x{h}" if w and h else "Résolution inconnue"
                        video_choices.append((base_url, media_template, init_template, fragment_count, res, info.get('id')))
                    except ValueError:
                        print(f"Valeurs de largeur/hauteur invalides : width={info.get('width')}, height={info.get('height')}")
                elif info.get('mimeType') == 'audio/mp4':
                    bw = info.get('bandwidth', 'Bande passante inconnue')
                    audio_choices.append((base_url, media_template, init_template, fragment_count, bw, info.get('id')))

    if not video_choices:
        print("Aucune option vidéo trouvée.")
        return None
    if not audio_choices:
        print("Aucune option audio trouvée.")
        return None

    def choose_best_video(v_choices):
        v_choices.sort(key=lambda x: int(x[4].split("x")[1]), reverse=True)
        return v_choices[0]

    def choose_best_audio(a_choices):
        a_choices.sort(key=lambda x: int(x[4]), reverse=True)
        return a_choices[0]

    chosen_video = choose_best_video(video_choices)
    print(f"Vidéo sélectionnée automatiquement avec la résolution {chosen_video[4]}.")
    chosen_audio = choose_best_audio(audio_choices)
    print(f"Audio sélectionné automatiquement avec une bande passante de {chosen_audio[4]} bps.")

    mp4_urls = []
    m4a_urls = []

    v_base, v_media, v_init, v_num, _, v_id = chosen_video
    mp4_urls.append(v_base + v_init.replace("$RepresentationID$", v_id))
    for i in range(1, v_num + 1):
        if v_media:
            mp4_urls.append(v_base + v_media.replace("$Number$", str(i)).replace("$RepresentationID$", v_id))
        else:
            print("Modèle de media manquant pour la vidéo.")

    a_base, a_media, a_init, a_num, _, a_id = chosen_audio
    m4a_urls.append(a_base + a_init.replace("$RepresentationID$", a_id))
    for i in range(1, a_num + 1):
        if a_media:
            m4a_urls.append(a_base + a_media.replace("$Number$", str(i)).replace("$RepresentationID$", a_id))
        else:
            print("Modèle de media manquant pour l'audio.")

    return [mp4_urls, m4a_urls]

class CrunchyrollClient:
    def __init__(self):
        self.access_token = None
        self.headers_android = ANDROID_HEADERS.copy()
        self.headers_desktop = FIREFOX_HEADERS.copy()

    def set_access_token(self, access_token: str):
        self.access_token = access_token
        self.headers_android["Authorization"] = f"Bearer {self.access_token}"

    def login(self, email: str, password: str) -> bool:
        url = f"{CRUNCHYROLL_API_BASE_URL}/auth/v1/token"
        data = {
            "username": email,
            "password": password,
            "grant_type": "password",
            "scope": "offline_access",
            "device_id": str(uuid.uuid4()),
            "device_name": "DUK-AL20",
            "device_type": "samsung%20SM-N975F"
        }
        response = requests.post(url, headers=AUTH_HEADERS, data=data)
        if response.status_code == 200:
            self.access_token = response.json().get("access_token")
            self.headers_android["Authorization"] = f"Bearer {self.access_token}"
            return True
        else:
            print(f"[red]Erreur d'authentification : {response.status_code} - {response.text}[/red]")
            return False

    def check_premium(self) -> bool:
        headers = self.headers_android
        r = requests.get(f"{CRUNCHYROLL_API_BASE_URL}/accounts/v1/me", headers=headers)
        if r.status_code != 200:
            return False
        external_id = r.json().get("external_id")
        if not external_id:
            return False
        url = f"{CRUNCHYROLL_API_BASE_URL}/subs/v1/subscriptions/{external_id}/benefits"
        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            return False
        source = r.text
        return "Subscription Not Found" not in source and "premium" in source

    def get_serie_id(self, crunchy_code: str) -> Optional[str]:
        url = f"{CRUNCHYROLL_STATIC_BASE_URL}/{crunchy_code}.json"
        headers = self.headers_desktop
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("credits", {}).get("seriesId")
        return None

    def get_all_seasons(self, series_id: str) -> Optional[Dict[str, List[str]]]:
        url = f"{CRUNCHYROLL_CMS_BASE_URL}/series/{series_id}/seasons?force_locale=fr-FR&preferred_audio_language=fr-FR&locale=fr-FR"
        headers = self.headers_desktop
        headers["Authorization"] = f"Bearer {self.access_token}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            ids = [item.get('id') for item in data.get('data', [])]
            titles = [item.get('title') for item in data.get('data', [])]
            return {"id": ids, "titles": titles}
        return None

    def get_all_episodes(self, season_id: str) -> Optional[List[Dict[str, Any]]]:
        url = f"{CRUNCHYROLL_CMS_BASE_URL}/seasons/{season_id}/episodes?preferred_audio_language=fr-FR&locale=fr-FR"
        headers = self.headers_desktop
        headers["Authorization"] = f"Bearer {self.access_token}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json().get('data', [])
            episodes = []
            for episode in data:
                title = episode.get('title')
                versions = episode.get('versions', [])
                audio_versions = []
                for version in versions:
                    audio_locale = version.get('audio_locale')
                    guid = version.get('guid')
                    audio_versions.append({'audio_locale': audio_locale, 'guid': guid})
                episodes.append({'title': title, 'audio_versions': audio_versions})
            return episodes
        return None

    def get_mpd_and_subtitles(self, guid: str) -> Optional[Dict[str, Any]]:
        url = f"{CRUNCHYROLL_PLAYBACK_BASE_URL}/{guid}/web/firefox/play"
        headers = self.headers_desktop
        headers["Authorization"] = f"Bearer {self.access_token}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 420:
            print(f"[red]Erreur détectée par Crunchyroll (Code 420) :[/red] {response.json().get('error')}")
            exit(1)
        else:
            print(f"[red]Code de réponse inattendu : {response.status_code}[/red]")
            exit(1)
        return None

    def add_data(self, guid: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        playback_data = self.get_mpd_and_subtitles(guid)
        if playback_data:
            return playback_data.get("url", ""), playback_data.get("subtitles", {})
        return None, None

    def fetch_mpd_data(self, url: str) -> Optional[str]:
        headers = self.headers_android
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"[red]Erreur lors de la récupération du MPD : {e}[/red]")
            return None


class Downloader:
    def __init__(self, client: CrunchyrollClient, desired_locales: str, selected_locale: Optional[str] = None):
        self.client = client
        self.desired_locales = desired_locales
        self.selected_locale = selected_locale

    def get_pssh(self, response_mpd: str) -> Optional[str]:
        return parse_lr(response_mpd, '<cenc:pssh>', '</cenc:pssh>', False)

    def get_priv_id(self, url: str) -> Optional[str]:
        return parse_lr(url, 'playbackGuid=', '&accountid', False)

    def download_and_concatenate_mpeg(self, list_of_urls: List[str], output_file: str):
        with open(output_file, 'wb') as out_file:
            with tqdm(total=len(list_of_urls), desc=f"Téléchargement vers {output_file}", unit="fichiers") as progress_bar:
                for url in list_of_urls:
                    try:
                        response = requests.get(url.strip(), impersonate="chrome")
                        response.raise_for_status()
                        out_file.write(response.content)
                        progress_bar.update(1)
                    except requests.exceptions.RequestException as e:
                        print(f"[red]Erreur de téléchargement pour URL {url}: {e}[/red]")

        print(f"Fichier créé avec succès : {output_file}")

    def get_key(self, pssh_base64: str, guid: str, priv_id: str) -> Optional[str]:
        pssh = PSSH(pssh_base64)
        device = Device.load(DEVICE_WVD_FILE)
        cdm = Cdm.from_device(device)
        session_id = cdm.open()
        challenge = cdm.get_license_challenge(session_id, pssh)
        headers = {
            "authorization": f"Bearer {self.client.access_token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "x-cr-content-id": guid,
            "x-cr-video-token": priv_id
        }
        licence = requests.post(CRUNCHYROLL_LICENSE_URL, data=challenge, headers=headers)
        response_json = licence.json()
        license_value = response_json.get("license")
        license_binary = base64.b64decode(license_value)

        cdm.parse_license(session_id, license_binary)

        content_key = None
        for key in cdm.get_keys(session_id):
            if key.type == "CONTENT":
                content_key = key.key.hex()
                break

        cdm.close(session_id)
        if content_key:
            return content_key
        else:
            print("[red]Aucune clé CONTENT trouvée.[/red]")
            return None

    def decrypt_video(self, decryption_key: str, input_file: str, output_file: str):
        command = [
            'ffmpeg',
            '-decryption_key', decryption_key,
            '-i', input_file,
            '-c', 'copy',
            output_file
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode == 0:
            print(f"Déchiffrement  réussi pour {output_file}.")
            os.remove(input_file)
        else:
            print(f"[red]Erreur lors du déchiffrement : {result.stderr}[/red]")

    def merge_audio_video(self, video_file: str, audio_file: str, output_file: str):
        command = [
            'ffmpeg',
            '-i', video_file,
            '-i', audio_file,
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-metadata:s:a:0', f'language={get_iso639_2(self.desired_locales)}',
            '-metadata:s:v:0', 'language=und',
            output_file
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode == 0:
            print(f"Mise en commun réussie. Fichier final : {output_file}")
            os.remove(video_file)
            os.remove(audio_file)
        else:
            print(f"[red]Erreur lors de la fusion : {result.stderr}[/red]")

    def download_subtitles(self, url: str, format: str, title: str, dir: str) -> bool:
        sanitized_title = sanitize_filename(title)
        filename = f"{sanitized_title}.{format}"
        file_path = os.path.join(dir, filename)
        try:
            response = requests.get(url)
            response.raise_for_status()
            with open(file_path, 'wb') as f:
                f.write(response.content)
            print(f"Sous-titres téléchargés et enregistrés dans '{filename}'")
            return True
        except requests.exceptions.RequestException as e:
            print(f"[red]Erreur lors du téléchargement des sous-titres : {e}[/red]")
            return False

    def add_subtitles(self, video_file: str, subtitles_file: str, output_file: str):
        if not self.selected_locale:
            print("[yellow]Aucune langue de sous-titres sélectionnée, sous-titres non ajoutés.[/yellow]")
            os.rename(video_file, output_file)
            return

        command = [
            'ffmpeg',
            '-i', video_file,
            '-i', subtitles_file,
            '-c', 'copy',
            '-c:s', 'mov_text',
            '-metadata:s:s:0', f'language={get_iso639_2(self.selected_locale)}',
            output_file
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode == 0:
            print(f"Sous-titres ajoutés avec succès. Fichier final : {output_file}")
            os.remove(video_file)
            os.remove(subtitles_file)
        else:
            print(f"[red]Erreur lors de l'ajout des sous-titres : {result.stderr}[/red]")


def get_iso639_2(locale):
    # https://fr.wikipedia.org/wiki/Liste_des_codes_ISO_639-2
    locale_to_iso639_2 = {
        "af-ZA": "afr", "am-ET": "amh", "ar-SA": "ara", "bg-BG": "bul", "bn-BD": "ben", "cs-CZ": "ces",
        "da-DK": "dan", "de-DE": "deu", "el-GR": "ell", "en-GB": "eng", "en-US": "eng",
        "es-419": "spa", "es-ES": "spa", "fa-IR": "fas", "fi-FI": "fin", "fr-CA": "fra",
        "fr-FR": "fra", "gu-IN": "guj", "he-IL": "heb", "hi-IN": "hin", "hu-HU": "hun",
        "id-ID": "ind", "it-IT": "ita", "ja-JP": "jpn", "kn-IN": "kan", "ko-KR": "kor",
        "ml-IN": "mal", "mr-IN": "mar", "ms-MY": "msa", "nl-NL": "nld", "no-NO": "nor",
        "pa-IN": "pan", "pl-PL": "pol", "pt-BR": "por", "pt-PT": "por", "ro-RO": "ron",
        "ru-RU": "rus", "sk-SK": "slk", "sv-SE": "swe", "sw-KE": "swa", "ta-IN": "tam",
        "te-IN": "tel", "th-TH": "tha", "tr-TR": "tur", "uk-UA": "ukr", "ur-PK": "urd",
        "vi-VN": "vie", "xh-ZA": "xho", "zh-CN": "zho", "zh-TW": "zho", "zu-ZA": "zul",
    }
    return locale_to_iso639_2.get(locale, "und")

def center_window(win: tk.Tk, width: int = 600, height: int = 400):
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = (sw // 2) - (width // 2)
    y = (sh // 2) - (height // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")

def choose_episodes_gui(filtered_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    customtkinter.set_appearance_mode("dark")
    customtkinter.set_default_color_theme("green")

    root = customtkinter.CTk()
    root.title("Choix des épisodes")
    center_window(root, 650, 500)

    main_container = customtkinter.CTkFrame(root, corner_radius=10)
    main_container.pack(fill="both", expand=True, padx=10, pady=10)

    episodes_vars = {}

    def on_continue():
        new_filtered_data = []
        for sdata in filtered_data:
            sname = sdata['season']
            ns = {"season": sname, "episodes": []}
            for (epdict, var) in episodes_vars[sname]:
                if var.get():
                    ns["episodes"].append(epdict)
            if ns["episodes"]:
                new_filtered_data.append(ns)
        root.selected = new_filtered_data
        root.quit()

    def toggle_all_episodes():
        all_selected = all(var.get() for slist in episodes_vars.values() for (_, var) in slist)
        new_state = not all_selected
        for slist in episodes_vars.values():
            for (_, var) in slist:
                var.set(new_state)

    top_controls = customtkinter.CTkFrame(main_container)
    top_controls.pack(fill="x", pady=5)

    global_toggle_btn = customtkinter.CTkButton(top_controls, text="Tout Sélectionner / Désélectionner", command=toggle_all_episodes, corner_radius=20)
    global_toggle_btn.pack(side="left", padx=5)

    scrollable_frame = customtkinter.CTkScrollableFrame(main_container, label_text="", corner_radius=10, width=600, height=350)
    scrollable_frame.pack(fill="both", expand=True, pady=5)

    for sdata in filtered_data:
        sname = sdata["season"]
        seps = sdata["episodes"]

        season_frame = customtkinter.CTkFrame(scrollable_frame, corner_radius=10, fg_color=("gray20", "gray25"))
        season_frame.pack(fill="x", pady=10, padx=10)

        header_frame = customtkinter.CTkFrame(season_frame, corner_radius=10)
        header_frame.pack(fill="x", pady=5)

        label_saison = customtkinter.CTkLabel(header_frame, text=sname, font=("Helvetica", 16, "bold"))
        label_saison.pack(side="left", padx=5)

        def make_toggle_fn(sn=sname):
            return lambda: toggle_season(sn)

        def toggle_season(sn):
            slist = episodes_vars[sn]
            all_selected = all(var.get() for (_, var) in slist)
            new_state = not all_selected
            for (_, var) in slist:
                var.set(new_state)

        toggle_btn = customtkinter.CTkButton(header_frame, text="Tout", command=make_toggle_fn(sname), corner_radius=15)
        toggle_btn.pack(side="right", padx=5)

        episodes_frame = customtkinter.CTkFrame(season_frame, corner_radius=0)
        episodes_frame.pack(fill="x", padx=20, pady=5)

        episodes_vars[sname] = []
        for i, epdict in enumerate(seps, start=1):
            epdict["number"] = i
            title = epdict.get("title", f"Épisode {i}")
            var = tk.BooleanVar(value=False)
            episodes_vars[sname].append((epdict, var))

            chk = customtkinter.CTkCheckBox(episodes_frame, text=f"{i}. {title}", variable=var, corner_radius=10)
            chk.pack(anchor="w", pady=2)

    continue_btn = customtkinter.CTkButton(root, text="Continuer", command=on_continue, corner_radius=20)
    continue_btn.pack(side="bottom", pady=10)

    root.selected = []
    root.mainloop()
    selected_episodes = root.selected
    root.destroy()
    return selected_episodes

def extract_local(episodes_by_season: List[Dict[str, Any]], desired_locales: str) -> List[Dict[str, Any]]:
    filtered_data = []
    for season in episodes_by_season:
        season_title = season.get('season')
        episodes = season.get('episodes', [])
        filtered_episodes = []
        for episode in episodes:
            title = episode.get('title')
            filtered_versions = [
                version for version in episode.get('audio_versions', [])
                if version.get('audio_locale') == desired_locales
            ]
            if filtered_versions:
                filtered_episodes.append({'title': title, 'audio_versions': filtered_versions})
        if filtered_episodes:
            filtered_data.append({'season': season_title, 'episodes': filtered_episodes})
    return filtered_data

def enrich_filtered_data_with_playback(filtered_data: List[Dict[str, Any]], client: CrunchyrollClient) -> List[Dict[str, Any]]:
    for season_data in filtered_data:
        for episode in season_data["episodes"]:
            guid = None
            versions = episode.get("audio_versions", [])
            if versions:
                guid = versions[0].get("guid")
            if not guid:
                continue
            playback_data = client.get_mpd_and_subtitles(guid)
            if playback_data:
                episode["mpd_url"] = playback_data.get("url", "")
                episode["subtitles"] = playback_data.get("subtitles", {})
    return filtered_data

def choose_subtitles_cli(filtered_data: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    first_episode_subtitles = filtered_data[0]["episodes"][0].get("subtitles", {})
    if not first_episode_subtitles:
        print("[yellow]Aucun sous-titre disponible pour le premier épisode.[/yellow]")
        return filtered_data, None

    available_subtitles = [loc for loc in first_episode_subtitles.keys() if loc.lower() != 'none']
    available_subtitles.append("Aucun sous-titres")

    print("\n[yellow]Sous-titres disponibles :[/yellow]")
    for idx, locale in enumerate(available_subtitles, start=1):
        print(f"[reset]{idx}. {locale}")

    try:
        choice = int(input("Choisissez la langue des sous-titres en entrant le numéro correspondant : "))
        if 1 <= choice <= len(available_subtitles):
            selected_locale = available_subtitles[choice - 1]
            if selected_locale == "Aucun sous-titres":
                for season_data in filtered_data:
                    for episode in season_data["episodes"]:
                        if "subtitles" in episode:
                            del episode["subtitles"]
                print("[yellow]Tous les sous-titres ont été supprimés.[/yellow]")
                return filtered_data, None
            else:
                for season_data in filtered_data:
                    for episode in season_data["episodes"]:
                        subtitles = episode.get("subtitles", {})
                        for loc in list(subtitles.keys()):
                            if loc != selected_locale:
                                del subtitles[loc]
                print(f"[yellow]Sous-titres conservés : {selected_locale}[/yellow]")
                return filtered_data, selected_locale
        else:
            print("[red]Choix invalide. Aucun changement effectué.[/red]")
            return filtered_data, None
    except ValueError:
        print("[red]Entrée invalide. Aucun changement effectué.[/red]")
        return filtered_data, None

def sanitize_filename(filename: str) -> str:
    forbidden_chars = r'[<>:"/\\|?*]'
    return re.sub(forbidden_chars, '-', filename)


def main():
    crunchyroll_client = CrunchyrollClient()

    # Le Login ne fonctionne plus...
    access_token = input("Entrez votre Access Token Crunchyroll: ")
    crunchyroll_client.set_access_token(access_token)

    if not crunchyroll_client.check_premium():
        print("[yellow4]L'Access Token ne semble pas valide ou ne donne pas accès à un abonnement premium.[/yellow4]")
        print("[yellow4]Veuillez vérifier votre Access Token.[/yellow4]")
        exit(1)

    crunchy_url = input("URL de l'anime Crunchyroll: ")
    if not re.match(REGEX_URL, crunchy_url):
        print("[bold red]Format d'URL invalide.[/bold red]")
        exit()
    
    crunchy_code = parse_lr(crunchy_url, 'watch/', '/', False)
    if not crunchy_code:
        print("[red]URL Crunchyroll invalide.[/red]")
        exit(1)

    series_id = crunchyroll_client.get_serie_id(crunchy_code)
    if not series_id:
        print("[red]Impossible de récupérer l'ID de la série.[/red]")
        exit(1)

    all_seasons_data = crunchyroll_client.get_all_seasons(series_id)
    if not all_seasons_data:
        print("[red]Impossible de récupérer les saisons.[/red]")
        exit(1)

    all_episodes_by_season = []
    for season_id, season_title in zip(all_seasons_data['id'], all_seasons_data['titles']):
        episodes = crunchyroll_client.get_all_episodes(season_id)
        if episodes:
            all_episodes_by_season.append({"season": season_title, "episodes": episodes})

    if not all_episodes_by_season:
        print("[red]Aucun épisode trouvé.[/red]")
        exit(1)

    available_locales = set()
    first_episode = all_episodes_by_season[0]['episodes'][0]
    for version in first_episode['audio_versions']:
        available_locales.add(version['audio_locale'])

    print("\n[yellow]Langues audio disponibles :[/yellow]")
    sorted_locales = sorted(available_locales)
    for idx, locale in enumerate(sorted_locales, start=1):
        print(f"[reset]{idx}. {locale}")

    try:
        choice = int(input("Choisissez la langue audio en entrant le numéro correspondant : "))
        if 1 <= choice <= len(sorted_locales):
            desired_locales = sorted_locales[choice - 1]
            print(f"[yellow]Langue audio sélectionnée : {desired_locales}[/yellow]")
        else:
            print("[red]Choix de langue audio invalide.[/red]")
            exit(1)
    except ValueError:
        print("[red]Entrée invalide pour la langue audio.[/red]")
        exit(1)

    filtered_data_by_locale = extract_local(all_episodes_by_season, desired_locales)
    selected_episodes_data = choose_episodes_gui(filtered_data_by_locale)
    if not selected_episodes_data:
        print("[yellow]Aucun épisode sélectionné pour le téléchargement.[/yellow]")
        exit(0)

    enriched_data = enrich_filtered_data_with_playback(selected_episodes_data, crunchyroll_client)
    final_filtered_data, selected_subtitle_locale = choose_subtitles_cli(enriched_data)

    downloader = Downloader(crunchyroll_client, desired_locales, selected_subtitle_locale)

    for season in final_filtered_data:
        season_name = season['season']
        output_dir = os.path.join(OUTPUT_DIR_BASE, sanitize_filename(season_name))
        os.makedirs(output_dir, exist_ok=True)

        for episode in season['episodes']:
            mpd_url = episode['mpd_url']
            title = episode['title']
            number = episode['number']
            guid = episode['audio_versions'][-1]['guid']

            subtitle_url = None
            subtitle_format = None
            if selected_subtitle_locale and 'subtitles' in episode and selected_subtitle_locale in episode['subtitles']:
                subtitle_data = episode['subtitles'][selected_subtitle_locale]
                subtitle_url = subtitle_data['url']
                subtitle_format = subtitle_data['format']

            url_mpd, _ = crunchyroll_client.add_data(guid)
            response_mpd = downloader.client.fetch_mpd_data(url_mpd)
            if not response_mpd:
                print(f"[red]Impossible de récupérer le MPD pour l'épisode {title}.[/red]")
                continue

            listmpeg = parse_mpd_content(response_mpd)
            if not listmpeg:
                print(f"[red]Impossible d'analyser le contenu MPD pour l'épisode {title}.[/red]")
                continue

            pssh_mpd = downloader.get_pssh(response_mpd)
            priv_id = downloader.get_priv_id(url_mpd)

            output_file_mp4_encrypted = os.path.join(output_dir, sanitize_filename(f"{number}. {title}-encrypted.mp4"))
            output_file_m4a_encrypted = os.path.join(output_dir, sanitize_filename(f"{number}. {title}-encrypted.m4a"))
            output_file_mp4_decrypted = os.path.join(output_dir, sanitize_filename(f"{number}. {title}-decrypted.mp4"))
            output_file_m4a_decrypted = os.path.join(output_dir, sanitize_filename(f"{number}. {title}-decrypted.m4a"))
            final_filename = sanitize_filename(f"{number}. {title}.mp4")
            final_path = os.path.join(output_dir, final_filename)
            temp_video_path = os.path.join(output_dir, sanitize_filename(f"{number}. {title}-without-subtitles.mp4"))


            downloader.download_and_concatenate_mpeg(listmpeg[0], output_file_mp4_encrypted)
            downloader.download_and_concatenate_mpeg(listmpeg[1], output_file_m4a_encrypted)

            keys = downloader.get_key(pssh_mpd, guid, priv_id)
            if keys:
                downloader.decrypt_video(keys, output_file_mp4_encrypted, output_file_mp4_decrypted)
                downloader.decrypt_video(keys, output_file_m4a_encrypted, output_file_m4a_decrypted)

                if selected_subtitle_locale and subtitle_url and subtitle_format:
                    downloader.merge_audio_video(output_file_mp4_decrypted, output_file_m4a_decrypted, temp_video_path)
                    subtitles_file_base = sanitize_filename(f'{title}_{selected_subtitle_locale}')
                    subtitles_file_path = os.path.join(output_dir, f"{subtitles_file_base}.{subtitle_format}")
                    if downloader.download_subtitles(subtitle_url, subtitle_format, subtitles_file_base, output_dir):
                        downloader.add_subtitles(temp_video_path, subtitles_file_path, final_path)
                    else:
                        print(f"[yellow]Sous-titres non ajoutés à {final_path} en raison d'une erreur de téléchargement.[/yellow]")
                        os.rename(temp_video_path, final_path)
                else:
                    downloader.merge_audio_video(output_file_mp4_decrypted, output_file_m4a_decrypted, final_path)
            else:
                print(f"[red]Clé de déchiffrement non obtenue pour l'épisode {title}. Déchiffrement impossible.[/red]")

    print("[green bold]Téléchargement terminé ![/green bold]")
    exit(0)

if __name__ == "__main__":
    main()
