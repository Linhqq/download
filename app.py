from flask import Flask, request, jsonify, send_file, abort
import requests
from bs4 import BeautifulSoup
import re
import struct
import os
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile
import threading
import time
import uuid

app = Flask(__name__)

max_threads = 10

headers = {
    "authority": "play2.cdn-xvideos-xnxx.xyz",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "vi-VN,vi;q=0.9,en-US;q=0.6,en;q=0.5",
    "cache-control": "max-age=0",
    "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="99"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36"
}

m3u8_headers = {
    "Referer": "https://play2.cdn-xvideos-xnxx.xyz/",
    "Origin": "https://play2.cdn-xvideos-xnxx.xyz",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": headers["user-agent"]
}

# Thư mục lưu file tạm
TMP_DIR = "/tmp/video_cache"
os.makedirs(TMP_DIR, exist_ok=True)

# Lưu metadata về thời gian tạo file để xoá sau 3 phút
file_time_map = {}
lock = threading.Lock()

def cleanup_expired_files():
    """Xoá các file đã quá 3 phút, chạy định kỳ"""
    while True:
        now = time.time()
        with lock:
            expired = [fname for fname, t in file_time_map.items() if now - t > 180]
            for fname in expired:
                path = os.path.join(TMP_DIR, fname)
                if os.path.exists(path):
                    os.remove(path)
                file_time_map.pop(fname)
        time.sleep(60)

# Chạy thread dọn dẹp khi app start
threading.Thread(target=cleanup_expired_files, daemon=True).start()

def extract_hidden_chunks(png_path):
    with open(png_path, 'rb') as f:
        data = f.read()

    pos = 8
    hidden_data = b''
    while pos < len(data):
        if pos + 8 > len(data):
            break
        length = struct.unpack('>I', data[pos:pos+4])[0]
        chunk_type = data[pos+4:pos+8]
        chunk_data = data[pos+8:pos+8+length]
        pos += 12 + length
        if chunk_type not in [b'IHDR', b'IDAT', b'IEND', b'PLTE', b'tEXt', b'zTXt', b'iTXt']:
            hidden_data += chunk_data
    return hidden_data

def download_image(img_url, folder):
    file_name = os.path.join(folder, os.path.basename(img_url))
    try:
        r = requests.get(img_url, headers=m3u8_headers, timeout=10)
        if r.status_code == 200:
            with open(file_name, "wb") as f:
                f.write(r.content)
            return file_name
    except Exception as e:
        print(f"Lỗi tải {img_url}: {e}")
    return None

# ... đoạn import, khai báo như trên ...

@app.route('/download_video', methods=['POST'])
def download_video():
    data = request.json
    if not data or "post_url" not in data:
        return jsonify({"error": "Missing 'post_url' in request JSON"}), 400

    post_url = data["post_url"]

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            html = requests.get(post_url, headers=headers).text
            soup = BeautifulSoup(html, "html.parser")
            meta_tag = soup.find("meta", itemprop="embedURL")
            if not meta_tag or not meta_tag.get("content"):
                return jsonify({"error": "Không tìm thấy embedURL"}), 404
            embed_url = meta_tag["content"]

            embed_resp = requests.get(embed_url, headers=headers)
            embed_html = embed_resp.text

            m3u8_links = re.findall(r'https?://[^\s"\']+\.m3u8[^\s"\']*', embed_html)
            if not m3u8_links:
                return jsonify({"error": "Không tìm thấy link m3u8 trong embed HTML"}), 404
            m3u8_url = m3u8_links[0]

            m3u8_text = requests.get(m3u8_url, headers=m3u8_headers).text
            image_urls = []
            for line in m3u8_text.splitlines():
                line = line.strip()
                if not line.endswith(".png"):
                    continue
                url_candidate = line if line.startswith("http") else urljoin(m3u8_url, line)

                # === XỬ LÝ FALLBACK REDIRECT ===
                if url_candidate.startswith("https://lh3-ggcontent.top"):
                    try:
                        # Gửi HEAD request để lấy URL redirect cuối cùng
                        resp = requests.head(url_candidate, headers=m3u8_headers, allow_redirects=True, timeout=10)
                        real_url = resp.url
                        # Kiểm tra nếu URL mới không phải lh3-ggcontent nữa thì lấy link thật
                        if real_url and not real_url.startswith("https://lh3-ggcontent.top"):
                            url_candidate = real_url
                    except Exception as e:
                        print(f"[!] Lỗi xử lý redirect cho {url_candidate}: {e}")

                image_urls.append(url_candidate)

            if not image_urls:
                return jsonify({"error": "Không tìm thấy ảnh PNG trong file M3U8"}), 404

            downloaded_files = []
            with ThreadPoolExecutor(max_threads) as executor:
                futures = [executor.submit(download_image, url, tmp_dir) for url in image_urls]
                for future in as_completed(futures):
                    path = future.result()
                    if path:
                        downloaded_files.append(path)

            if not downloaded_files:
                return jsonify({"error": "Tải ảnh PNG thất bại"}), 500

            all_data = b''
            for png_file in sorted(downloaded_files):
                hidden_data = extract_hidden_chunks(png_file)
                if hidden_data:
                    all_data += hidden_data

            if not all_data:
                return jsonify({"error": "Không trích xuất được dữ liệu video từ ảnh PNG"}), 500

            file_id = str(uuid.uuid4())
            file_name = f"{file_id}.mp4"
            file_path = os.path.join(TMP_DIR, file_name)
            with open(file_path, "wb") as f:
                f.write(all_data)

            with lock:
                file_time_map[file_name] = time.time()

            download_link = f"/download/{file_id}"
            return jsonify({
                "message": "Video đã sẵn sàng để tải về",
                "download_url": download_link,
                "file_size_bytes": len(all_data)
            })

        except Exception as e:
            return jsonify({"error": f"Lỗi khi tải video: {str(e)}"}), 500

@app.route('/download/<file_id>', methods=['GET'])
def serve_file(file_id):
    file_name = f"{file_id}.mp4"
    file_path = os.path.join(TMP_DIR, file_name)

    with lock:
        t = file_time_map.get(file_name)
        if not t:
            return abort(404, description="File không tồn tại hoặc đã hết hạn.")
        if time.time() - t > 180:
            # Xoá file quá hạn
            if os.path.exists(file_path):
                os.remove(file_path)
            file_time_map.pop(file_name, None)
            return abort(404, description="File đã hết hạn.")

    if os.path.exists(file_path):
        return send_file(file_path, mimetype="video/mp4", as_attachment=True, download_name="video.mp4")
    else:
        return abort(404, description="File không tồn tại.")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
