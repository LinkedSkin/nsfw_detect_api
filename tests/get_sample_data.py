

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

import os
import requests
from datasets import load_dataset

def download_fixture_images(label: str, count: int, out_dir: str, prefix: str):
    os.makedirs(out_dir, exist_ok=True)
    dataset = load_dataset("deepghs/nsfw_detect", split="train", streaming=True)
    logger.info(f"Downloading {count} images with label={label} to {out_dir}")
    filtered = (x for x in dataset if x['label'] == label)
    for i, sample in enumerate(filtered):
        if i >= count:
            break
        url = sample["image"]["url"]
        filename = f"{prefix}_sample_{i + 1}.jpg"
        dest_path = os.path.join(out_dir, filename)

        response = requests.get(url)
        if response.status_code != 200:
            logger.error(f"Failed to download image from {url}, status code: {response.status_code}")
            raise RuntimeError(f"Failed to download image from {url}")

        with open(dest_path, "wb") as f:
            f.write(response.content)

        logger.info(f"Downloaded: {dest_path}")

if __name__ == "__main__":
    logger.info("Starting fixture download script")
    # Download 3 nude images
    download_fixture_images(label="porn", count=3, out_dir="tests/fixtures", prefix="nude")

    # Download 3 non-nude images
    download_fixture_images(label="neutral", count=3, out_dir="tests/fixtures", prefix="safe")
    logger.info("Fixture download script completed")