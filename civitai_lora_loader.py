import json
import os
import requests
import sys
from tqdm import tqdm

import folder_paths
import comfy.utils
from nodes import LoraLoader


ROOT_PATH = os.path.dirname(os.path.abspath(__file__))
LORA_PATH = folder_paths.folder_names_and_paths["loras"][0][0]

MSG_PREFIX = '\33[1m\33[34m[CivitAI] \33[0m'
ERR_PREFIX = '\33[1m\33[31m[CivitAI]\33[0m\33[1m Error: \33[0m'


class CivitAI_Model:
    """
        © Copyright 2023 CivitAI
        Written by Jordan Thompson
        
        Simple class for fetching CivitAI loras by model ID and model version ID if it exists. 
    """
    api = 'https://civitai.com/api/v1'

    def __init__(self, model_id, version=None):
    
        self.model_id = model_id
        self.version = version
        self.name = None
        self.download_url = None
        self.file_details = None
        
        self.details()

    def details(self):
        model_url = f'{self.api}/models/{self.model_id}'
        response = requests.get(model_url)

        if response.status_code == 200:
            model_data = response.json()

            model_versions = model_data.get('modelVersions')
            model_type = model_data.get('type')

            if model_type != 'LORA':
                raise Exception(f"{ERR_PREFIX}The model you requested is not a valid LORA. Aborting!")

            if not self.version:
                latest_version = max(model_versions, key=lambda x: x['id'])
                self.version = latest_version['id']
                files = latest_version.get('files', {})

            else:
                files = None
                for version in model_versions:
                    version_files = version.get('files')
                    if version_files:
                        for file in version_files:
                            if file['id'] == int(self.version):
                                self.name = file['name']
                                self.download_url = file['downloadUrl']
                                self.file_details = file
                                return self.download_url, file

                if files is None:
                    raise Exception(f"{ERR_PREFIX}The specified version {self.version} was not found.")

            if files:
                self.name = files[0]['name']
                self.download_url = files[0]['downloadUrl']
                return self.download_url, files[0]

        else:
            response.raise_for_status()

    def download(self):
        lora_name = self.lora_cached_name(self.model_id, self.version)
        
        if lora_name and os.path.exists(os.path.join(LORA_PATH, lora_name)):
            print(f"{MSG_PREFIX}Loading lora from disk: {os.path.join(LORA_PATH, lora_name)}")
            self.name = lora_name
            return True

        if not self.name:
            response = requests.head(self.download_url)
            if 'Content-Disposition' in response.headers:
                content_disposition = response.headers['Content-Disposition']
                self.name = re.findall("filename=(.+)", content_disposition)[0].strip('"')
            else:
                self.name = self.download_url.split('/')[-1]

        print(f"{MSG_PREFIX}Downloading `{self.name}` from `{self.download_url}`")
        save_path = os.path.join(LORA_PATH, self.name)

        if os.path.exists(save_path):
            print(f"{MSG_PREFIX}Lora file already exists at: {save_path}")
            self.dump_file_details()
            return True

        response = requests.get(self.download_url, stream=True)

        if response.status_code == requests.codes.ok:
            file_size = int(response.headers.get('Content-Length', 0))

            with open(save_path, 'wb') as file:
                pbar = comfy.utils.ProgressBar(file_size)
                pbar.update(0)

                for chunk in response.iter_content(chunk_size=1024):
                    file.write(chunk)
                    pbar.update(len(chunk))

            print(f"{MSG_PREFIX}Lora saved at: {save_path}")
            self.dump_file_details()
            return True

        elif response.status_code == requests.codes.not_found:
            print(f"{ERR_PREFIX}CivitAI is not reachable, or the file was not found.")
        else:
            print(f"{ERR_PREFIX}Failed to download Lora file from CivitAI. Status code: {response.status_code}")

        return False
        
    def dump_file_details(self):
        history_file_path = os.path.join(ROOT_PATH, 'download_history.json')

        if os.path.exists(history_file_path):
            with open(history_file_path, 'r', encoding='utf-8') as history_file:
                download_history = json.load(history_file)

                if str(self.model_id) in download_history:
                    download_history[str(self.model_id)].append(self.file_details)
                else:
                    download_history[str(self.model_id)] = [self.file_details]
        else:
            download_history = {str(self.model_id): [self.file_details]}

        with open(history_file_path, 'w', encoding='utf-8') as history_file:
            json.dump(download_history, history_file, indent=4, ensure_ascii=False)
            
    def lora_cached_name(self, model_id, version_id):
        history_file_path = os.path.join(ROOT_PATH, 'download_history.json')

        if os.path.exists(history_file_path):
            with open(history_file_path, 'r') as history_file:
                download_history = json.load(history_file)

                if str(model_id) in download_history:
                    file_details = download_history[str(model_id)]

                    for file in file_details:
                        version = file.get('id')
                        name = file.get('name')
                        if version == int(version_id):
                            return name

        return None


class CivitAI_LORA_Loader:
    """
        Implements the CivitAI LORA Loader node for ComfyUI 
    """
    def __init__(self):
        self.lora_loader = None

    @classmethod
    def INPUT_TYPES(cls):
        loras = folder_paths.get_filename_list("loras")
        loras.insert(0, 'none')
        
        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP", ),
                "lora_slug": ("STRING", {"default": "{model_id}@{model_version}", "multiline": False}),
                "lora_name": (loras,),
                "strength_model": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.01}),
                "strength_clip": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.01}),

            }
        }

    RETURN_TYPES = ("MODEL", "CLIP")
    FUNCTION = "load_lora"

    CATEGORY = "CivitAI/Loader"

    def load_lora(self, model, clip, lora_slug, lora_name, strength_model, strength_clip):

        if not self.lora_loader:
            self.lora_loader = LoraLoader()
            
        if lora_name == 'none':
        
            lora_id = None
            version_id = None
            
            if '@' in lora_slug:
                lora_id, version_id = lora_slug.split('@')
            else:
                lora_id = lora_slug
                
            lora_id = int(lora_id) if lora_id else lora_id
            version_id = int(version_id) if version_id else version_id
            
            civitai_model = CivitAI_Model(lora_id, version_id)
                
            if not civitai_model.download():
               return model, clip 
               
            lora_name = civitai_model.name
        
        model_lora, clip_lora = self.lora_loader.load_lora(model, clip, lora_name, strength_model, strength_clip)

        return ( model_lora, clip_lora )
        

NODE_CLASS_MAPPINGS = {
    "CivitAI_Lora_Loader": CivitAI_LORA_Loader
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CivitAI_Lora_Loader": "CivitAI Lora Loader"
}