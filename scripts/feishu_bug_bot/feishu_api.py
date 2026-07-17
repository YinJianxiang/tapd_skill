"""飞书开放平台：token、发消息、下载图片。仅标准库。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: str = ""
        self._token_expire_at: float = 0.0

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        raw_body: Optional[bytes] = None,
        binary: bool = False,
    ) -> Any:
        url = f"{FEISHU_BASE}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        hdrs = {"Content-Type": "application/json; charset=utf-8"}
        if headers:
            hdrs.update(headers)
        body = raw_body
        if data is not None and body is None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                content = resp.read()
                if binary:
                    return content
                return json.loads(content.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"飞书 API HTTP {e.code}: {err_body}") from e

    def get_tenant_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expire_at - 60:
            return self._token
        result = self._request(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            data={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        token = result.get("tenant_access_token") or ""
        code = result.get("code")
        if not token or (code is not None and code != 0):
            raise RuntimeError(f"获取 tenant_access_token 失败: {result}")
        expire = int(result.get("expire") or 7200)
        self._token = token
        self._token_expire_at = now + expire
        return token

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.get_tenant_access_token()}"}

    def send_text(self, receive_id: str, text: str, receive_id_type: str = "open_id") -> Dict[str, Any]:
        return self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            data={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            headers=self._auth_headers(),
        )

    def send_interactive(
        self,
        receive_id: str,
        card: Dict[str, Any],
        receive_id_type: str = "open_id",
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            data={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
            headers=self._auth_headers(),
        )

    def download_message_resource(
        self,
        message_id: str,
        file_key: str,
        *,
        resource_type: str = "image",
        dest_path: Path,
    ) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        content = self._request(
            "GET",
            f"/im/v1/messages/{message_id}/resources/{file_key}",
            params={"type": resource_type},
            headers=self._auth_headers(),
            binary=True,
        )
        dest_path.write_bytes(content)
        return dest_path


def aes_decrypt_feishu(encrypt: str, encrypt_key: str) -> bytes:
    """
    飞书加密：key = SHA256(encrypt_key)，密文 base64，前 16 字节为 IV。
    使用标准库纯 Python AES（仅 CBC PKCS7），实现见下方 _AESCipher。
    """
    import base64
    import hashlib

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    raw = base64.b64decode(encrypt)
    iv, ciphertext = raw[:16], raw[16:]
    return _aes_cbc_pkcs7_decrypt(ciphertext, key, iv)


# ---- 最小 AES-CBC（仅解密）实现，避免引入第三方包 ----
# 基于公钥域标准实现的精简版

def _aes_cbc_pkcs7_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    try:
        from Crypto.Cipher import AES  # type: ignore

        cipher = AES.new(key, AES.MODE_CBC, iv)
        plain = cipher.decrypt(ciphertext)
        pad = plain[-1]
        if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
            return plain[:-pad]
        return plain
    except ImportError:
        pass
    # 无 pycryptodome 时用 openssl 不可靠；改用纯 Python AES
    return _pure_aes_cbc_decrypt(ciphertext, key, iv)


def _pure_aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    aes = _AES(key)
    blocks = [ciphertext[i : i + 16] for i in range(0, len(ciphertext), 16)]
    plain = b""
    prev = iv
    for block in blocks:
        decrypted = aes.decrypt_block(block)
        plain += bytes(a ^ b for a, b in zip(decrypted, prev))
        prev = block
    pad = plain[-1]
    if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        return plain[:-pad]
    return plain


class _AES:
    """极简 AES-128/192/256 单块解密。"""

    SBOX = [
        99,124,119,123,242,107,111,197,48,1,103,43,254,215,171,118,202,130,201,125,
        250,89,71,240,173,212,162,175,156,164,114,192,183,253,147,38,54,63,247,204,
        52,165,229,241,113,216,49,21,4,199,35,195,24,150,5,154,7,18,128,226,235,39,
        178,117,9,131,44,26,27,110,90,160,82,59,214,179,41,227,47,132,83,209,0,237,
        32,252,177,91,106,203,190,57,74,76,88,207,208,239,170,251,67,77,51,133,69,249,
        2,127,80,60,159,168,81,163,64,143,146,157,56,245,188,182,218,33,16,255,243,210,
        205,12,19,236,95,151,68,23,196,167,126,61,100,93,25,115,96,129,79,220,34,42,
        144,136,70,238,184,20,222,94,11,219,224,50,58,10,73,6,36,92,194,211,172,98,
        145,149,228,121,231,200,55,109,141,213,78,169,108,86,244,234,101,122,174,8,
        186,120,37,46,28,166,180,198,232,221,116,31,75,189,139,138,112,62,181,102,72,
        3,246,14,97,53,87,185,134,193,29,158,225,248,152,17,105,217,142,148,155,30,
        135,233,206,85,40,223,140,161,137,13,191,230,66,104,65,153,45,15,176,84,187,22,
    ]
    RSBOX = [0] * 256
    for i, v in enumerate(SBOX):
        RSBOX[v] = i
    RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]

    def __init__(self, key: bytes) -> None:
        self.nk = len(key) // 4
        self.nr = {4: 10, 6: 12, 8: 14}[self.nk]
        self.round_keys = self._key_expansion(key)

    def _key_expansion(self, key: bytes) -> List[List[int]]:
        w: List[List[int]] = []
        for i in range(self.nk):
            w.append(list(key[4 * i : 4 * i + 4]))
        for i in range(self.nk, 4 * (self.nr + 1)):
            temp = w[i - 1][:]
            if i % self.nk == 0:
                temp = temp[1:] + temp[:1]
                temp = [self.SBOX[b] for b in temp]
                temp[0] ^= self.RCON[i // self.nk]
            elif self.nk > 6 and i % self.nk == 4:
                temp = [self.SBOX[b] for b in temp]
            w.append([w[i - self.nk][j] ^ temp[j] for j in range(4)])
        return w

    def decrypt_block(self, block: bytes) -> bytes:
        state = [list(block[i : i + 4]) for i in range(0, 16, 4)]
        # 转置为列主序 4x4
        s = [[state[r][c] for r in range(4)] for c in range(4)]
        self._add_round_key(s, self.nr)
        for rnd in range(self.nr - 1, 0, -1):
            self._inv_shift_rows(s)
            self._inv_sub_bytes(s)
            self._add_round_key(s, rnd)
            self._inv_mix_columns(s)
        self._inv_shift_rows(s)
        self._inv_sub_bytes(s)
        self._add_round_key(s, 0)
        out = bytearray(16)
        for c in range(4):
            for r in range(4):
                out[c * 4 + r] = s[r][c]
        return bytes(out)

    def _add_round_key(self, s: List[List[int]], rnd: int) -> None:
        for c in range(4):
            for r in range(4):
                s[r][c] ^= self.round_keys[rnd * 4 + c][r]

    def _inv_sub_bytes(self, s: List[List[int]]) -> None:
        for r in range(4):
            for c in range(4):
                s[r][c] = self.RSBOX[s[r][c]]

    def _inv_shift_rows(self, s: List[List[int]]) -> None:
        s[1] = s[1][3:] + s[1][:3]
        s[2] = s[2][2:] + s[2][:2]
        s[3] = s[3][1:] + s[3][:1]

    @staticmethod
    def _xtime(a: int) -> int:
        return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF

    @classmethod
    def _mul(cls, a: int, b: int) -> int:
        res = 0
        for _ in range(8):
            if b & 1:
                res ^= a
            a = cls._xtime(a)
            b >>= 1
        return res & 0xFF

    def _inv_mix_columns(self, s: List[List[int]]) -> None:
        for c in range(4):
            a0, a1, a2, a3 = s[0][c], s[1][c], s[2][c], s[3][c]
            s[0][c] = self._mul(a0, 0x0E) ^ self._mul(a1, 0x0B) ^ self._mul(a2, 0x0D) ^ self._mul(a3, 0x09)
            s[1][c] = self._mul(a0, 0x09) ^ self._mul(a1, 0x0E) ^ self._mul(a2, 0x0B) ^ self._mul(a3, 0x0D)
            s[2][c] = self._mul(a0, 0x0D) ^ self._mul(a1, 0x09) ^ self._mul(a2, 0x0E) ^ self._mul(a3, 0x0B)
            s[3][c] = self._mul(a0, 0x0B) ^ self._mul(a1, 0x0D) ^ self._mul(a2, 0x09) ^ self._mul(a3, 0x0E)


def parse_message_content(msg_type: str, content_raw: str) -> Tuple[str, List[str]]:
    """返回 (text, image_keys)。"""
    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
    except json.JSONDecodeError:
        return str(content_raw), []

    texts: List[str] = []
    images: List[str] = []

    if msg_type == "text":
        texts.append(str(content.get("text") or ""))
    elif msg_type == "image":
        key = content.get("image_key") or content.get("file_key")
        if key:
            images.append(str(key))
    elif msg_type == "post":
        # post: {zh_cn: {title, content: [[{tag, text/image_key}]]}}
        for lang_body in content.values():
            if not isinstance(lang_body, dict):
                continue
            title = lang_body.get("title")
            if title:
                texts.append(str(title))
            for line in lang_body.get("content") or []:
                for elem in line:
                    if not isinstance(elem, dict):
                        continue
                    tag = elem.get("tag")
                    if tag == "text":
                        texts.append(str(elem.get("text") or ""))
                    elif tag == "img":
                        key = elem.get("image_key")
                        if key:
                            images.append(str(key))
    elif msg_type == "interactive":
        texts.append("[interactive card]")
    else:
        texts.append(json.dumps(content, ensure_ascii=False))

    return "\n".join(t for t in texts if t).strip(), images
