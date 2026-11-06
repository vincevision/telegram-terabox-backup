import os
import time
import json
import hashlib
import re
import random
import base64
import requests
from typing import Optional
from config import Config


class TeraBoxAPI:
    """
    TeraBox API handler with full browser headers, logid generation,
    and fallback jsToken handling.
    """

    BASE_URL = "https://www.terabox.com"
    API_URL = f"{BASE_URL}/api"

    def __init__(self, cookie: str):
        self.session = requests.Session()
        
        ndus_match = re.search(r'ndus=([^;]+)', cookie)
        self.ndus = ndus_match.group(1) if ndus_match else ""

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Cookie": cookie,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.terabox.com",
            "Referer": "https://www.terabox.com/main",
            "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest",
        })
        self._bdstoken = None
        self._jstoken = Config.TERABOX_JSTOKEN or None
        self._init_tokens()

    def _generate_logid(self) -> str:
        return base64.b64encode(str(random.random()).encode()).decode()

    def _init_tokens(self):
        """Fetch required security tokens from TeraBox."""
        try:
            # Fetch bdstoken & jsToken via API endpoint
            url = f"{self.API_URL}/gettemplatevariable"
            params = {
                "clienttype": "0",
                "app_id": "250528",
                "web": "1",
                "fields": '["jsToken","bdstoken","uk"]'
            }
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if data.get("errno") == 0:
                        result = data.get("result", {})
                        if not self._bdstoken:
                            self._bdstoken = result.get("bdstoken")
                        if not self._jstoken:
                            self._jstoken = result.get("jsToken")
                except Exception:
                    pass

            # Fallback regex search on main page
            if not self._bdstoken or not self._jstoken:
                main_resp = self.session.get(f"{self.BASE_URL}/main", timeout=15)
                html = main_resp.text

                if not self._bdstoken:
                    m = re.search(r'["\']bdstoken["\']\s*:\s*["\']([a-f0-9]+)["\']', html, re.I)
                    if m:
                        self._bdstoken = m.group(1)

                if not self._jstoken:
                    patterns = [
                        r'["\']jsToken["\']\s*:\s*["\']([a-f0-9]+)["\']',
                        r'jsToken\s*=\s*["\']([a-f0-9]+)["\']',
                        r'fn_jsToken[^{]*\{\s*return\s*["\']([a-f0-9]+)["\']'
                    ]
                    for pat in patterns:
                        m_js = re.search(pat, html, re.I)
                        if m_js:
                            self._jstoken = m_js.group(1)
                            break

            status_bds = "FOUND" if self._bdstoken else "MISSING"
            status_js = "FOUND" if self._jstoken else "MISSING"
            print(f"[TeraBox] Token Init: bdstoken={status_bds}, jsToken={status_js}")
        except Exception as e:
            print(f"[TeraBox] Token init error: {e}")

    def _api_params(self) -> dict:
        params = {
            "app_id": "250528",
            "web": "1",
            "channel": "dubox",
            "clienttype": "0",
            "logid": self._generate_logid(),
        }
        if self._bdstoken:
            params["bdstoken"] = self._bdstoken
        if self._jstoken:
            params["jsToken"] = self._jstoken
        return params

    def get_quota(self) -> Optional[dict]:
        try:
            params = self._api_params()
            params["checkfree"] = "1"
            resp = self.session.get(f"{self.API_URL}/quota", params=params)
            data = resp.json()
            if data.get("errno") == 0:
                return {
                    "total": data.get("total", 0),
                    "used": data.get("used", 0),
                    "free": data.get("total", 0) - data.get("used", 0),
                }
        except Exception as e:
            print(f"[TeraBox] Quota error: {e}")
        return None

    def create_folder(self, folder_path: str) -> bool:
        try:
            params = self._api_params()
            params["a"] = "commit"
            data = {
                "path": folder_path,
                "isdir": "1",
                "block_list": "[]",
            }
            resp = self.session.post(
                f"{self.API_URL}/create", params=params, data=data
            )
            result = resp.json()
            return result.get("errno") in [0, -8]
        except Exception as e:
            print(f"[TeraBox] Create folder error: {e}")
            return False

    def _precreate(self, remote_path: str, file_size: int, block_list: list) -> Optional[dict]:
        try:
            params = self._api_params()
            data = {
                "path": remote_path,
                "size": str(file_size),
                "isdir": "0",
                "autoinit": "1",
                "block_list": json.dumps(block_list),
                "rtype": "1"
            }
            resp = self.session.post(
                f"{self.API_URL}/precreate", params=params, data=data
            )
            result = resp.json()
            if result.get("errno") == 0:
                return result
            print(f"[TeraBox] API Precreate Error {result.get('errno')}: {result.get('errmsg')}")
            return None
        except Exception as e:
            print(f"[TeraBox] Precreate failed: {e}")
            return None

    def _upload_chunk(self, upload_id: str, remote_path: str,
                      chunk_data: bytes, part_seq: int) -> bool:
        try:
            params = {
                "method": "upload",
                "app_id": "250528",
                "channel": "dubox",
                "clienttype": "0",
                "web": "1",
                "path": remote_path,
                "uploadid": upload_id,
                "partseq": str(part_seq),
                "logid": self._generate_logid(),
            }
            if self._bdstoken:
                params["bdstoken"] = self._bdstoken
            if self._jstoken:
                params["jsToken"] = self._jstoken

            files = {"file": ("blob", chunk_data, "application/octet-stream")}
            resp = self.session.post(
                "https://c-jp.terabox.com/rest/2.0/pcs/superfile2",
                params=params,
                files=files,
                timeout=300
            )
            return "md5" in resp.text
        except Exception as e:
            print(f"[TeraBox] Chunk upload error: {e}")
            return False

    def _create_file(self, remote_path: str, file_size: int,
                     upload_id: str, block_list: list) -> bool:
        try:
            params = self._api_params()
            data = {
                "path": remote_path,
                "size": str(file_size),
                "isdir": "0",
                "uploadid": upload_id,
                "block_list": json.dumps(block_list),
                "rtype": "1"
            }
            resp = self.session.post(
                f"{self.API_URL}/create", params=params, data=data
            )
            result = resp.json()
            return result.get("errno") == 0
        except Exception as e:
            print(f"[TeraBox] Finalize create_file error: {e}")
            return False

    def upload_file(self, local_path: str, remote_path: str,
                    progress_callback=None) -> bool:
        try:
            file_size = os.path.getsize(local_path)
            chunk_size = 4 * 1024 * 1024

            block_md5_list = []
            with open(local_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    block_md5_list.append(hashlib.md5(chunk).hexdigest())

            precreate_result = self._precreate(remote_path, file_size, block_md5_list)
            if not precreate_result:
                return False

            upload_id = precreate_result.get("uploadid")

            if precreate_result.get("return_type") == 2:
                print(f"[TeraBox] Rapid upload hit: {remote_path}")
                if progress_callback:
                    progress_callback(file_size, file_size)
                return True

            uploaded_bytes = 0
            with open(local_path, "rb") as f:
                for i, _ in enumerate(block_md5_list):
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    success = self._upload_chunk(upload_id, remote_path, chunk, i)
                    if not success:
                        print(f"[TeraBox] Failed uploading chunk {i}")
                        return False

                    uploaded_bytes += len(chunk)
                    if progress_callback:
                        progress_callback(uploaded_bytes, file_size)

            return self._create_file(remote_path, file_size, upload_id, block_md5_list)

        except Exception as e:
            print(f"[TeraBox] Upload error: {e}")
            return False

    def check_connection(self) -> bool:
        return self.get_quota() is not None