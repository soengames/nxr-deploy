from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import socket
import json
import io
import asyncio
import subprocess
import sys
import traceback

try:
    import edge_tts
except ImportError:
    print("[edge-tts] 미설치 감지 — 자동 설치 중...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "edge-tts"])
    import edge_tts
    print("[edge-tts] 설치 완료")


async def _generate_mp3(text, voice, rate, pitch, volume) -> bytes:
    communicate = edge_tts.Communicate(
        text=text, voice=voice, rate=rate, pitch=pitch, volume=volume
    )
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


class UnityWebGLHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".js":   "application/javascript",
        ".json": "application/json",
        ".data": "application/octet-stream",
    }

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma",  "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        if self.path == "/tts":
            self._handle_tts()
        else:
            self.send_error(404, "Not Found")

    def _handle_tts(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._respond_error(400, "Invalid JSON")
            return

        text = req.get("text", "").strip()
        if not text:
            self._respond_error(400, "text is empty")
            return

        # log incoming request briefly
        print(f"[TTS] request text_len={len(text)} voice={req.get('voice','-')} rate={req.get('rate','-')} pitch={req.get('pitch','-')}")

        try:
            mp3 = asyncio.run(_generate_mp3(
                text,
                req.get("voice",  "ko-KR-SunHiNeural"),
                req.get("rate",   "+0%"),
                req.get("pitch",  "+0Hz"),
                req.get("volume", "+0%"),
            ))
        except Exception as e:
            # If edge-tts returned NoAudioReceived, try a safe fallback (default voice, no prosody)
            try:
                if hasattr(edge_tts, 'exceptions') and getattr(edge_tts.exceptions, 'NoAudioReceived', None) is not None and isinstance(e, edge_tts.exceptions.NoAudioReceived):
                    traceback.print_exc()
                    print('[TTS] NoAudioReceived from edge-tts — retrying with default voice and no prosody')
                    try:
                        mp3 = asyncio.run(_generate_mp3(text, 'ko-KR-SunHiNeural', '+0%', '+0Hz', '+0%'))
                    except Exception as e2:
                        traceback.print_exc()
                        self._respond_error(500, f"No audio after retry: {e2}")
                        return
                else:
                    traceback.print_exc()
                    self._respond_error(500, str(e))
                    return
            except Exception:
                # defensive: if checking or retry itself fails
                traceback.print_exc()
                self._respond_error(500, str(e))
                return

        if not mp3:
            self._respond_error(500, "TTS generated empty audio")
            return

        self.send_response(200)
        self.send_header("Content-Type",   "audio/mpeg")
        self.send_header("Content-Length", str(len(mp3)))
        self.end_headers()
        self.wfile.write(mp3)
        # log success
        print(f"[TTS] POST /tts HTTP/1.1 200 len={len(mp3)}")

    def _respond_error(self, code, message):
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if self.path == "/tts":
            print(f"[TTS] {args[0]} {args[1]}")
        else:
            super().log_message(fmt, *args)


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    port     = 8000
    host     = "0.0.0.0"
    local_ip = get_local_ip()

    httpd = ThreadingHTTPServer((host, port), UnityWebGLHandler)

    print(f"Serving locally at   http://127.0.0.1:{port}")
    print(f"Serving on LAN at    http://{local_ip}:{port}")
    print(f"TTS endpoint         POST http://127.0.0.1:{port}/tts")

    httpd.serve_forever()