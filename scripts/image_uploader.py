"""
Image uploader using Cloudinary (25GB free tier).
"""

import os
import requests
import hashlib
import time
from typing import Optional

# Try to load .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass


def upload_to_cloudinary(image_url: str, cloud_name: Optional[str] = None,
                          api_key: Optional[str] = None, api_secret: Optional[str] = None) -> Optional[str]:
    """
    Upload image to Cloudinary.

    Args:
        image_url: URL of image to upload
        cloud_name: Cloudinary cloud name
        api_key: Cloudinary API key
        api_secret: Cloudinary API secret

    Returns: Permanent Cloudinary URL or None on failure
    """
    cloud_name = cloud_name or os.environ.get("CLOUDINARY_CLOUD_NAME")
    api_key = api_key or os.environ.get("CLOUDINARY_API_KEY")
    api_secret = api_secret or os.environ.get("CLOUDINARY_API_SECRET")

    if not all([cloud_name, api_key, api_secret]):
        print("[cloudinary] Missing CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, or CLOUDINARY_API_SECRET")
        return None

    try:
        timestamp = int(time.time())
        params = f"timestamp={timestamp}{api_secret}"
        signature = hashlib.sha1(params.encode()).hexdigest()

        # Download image locally first — avoids Cloudinary fetching expired URLs
        img_resp = requests.get(image_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if img_resp.status_code != 200:
            print(f"[cloudinary] Failed to download image: {img_resp.status_code} {image_url}")
            return None
        img_bytes = img_resp.content

        resp = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
            data={
                "api_key": api_key,
                "timestamp": timestamp,
                "signature": signature,
            },
            files={"file": ("image.jpg", img_bytes, "image/jpeg")},
            timeout=60
        )

        if resp.status_code == 200:
            data = resp.json()
            url = data.get("secure_url")
            if url:
                print(f"[cloudinary] Uploaded: {url}")
                return url

        print(f"[cloudinary] Failed: {resp.status_code} {resp.text[:200]}")
        return None

    except Exception as e:
        print(f"[cloudinary] Error: {e}")
        return None


def upload_image(image_url: str) -> Optional[str]:
    """Upload image to Cloudinary."""
    return upload_to_cloudinary(image_url)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python image_uploader.py <image_url>")
        sys.exit(1)

    result = upload_image(sys.argv[1])
    if result:
        print(f"\n✅ Result: {result}")
    else:
        print("\n❌ Upload failed")
        sys.exit(1)
