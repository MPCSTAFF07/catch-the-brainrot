from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PHOTO_BYTES = 900 * 1024
MAX_OFFER_REQUEST_BYTES = 980 * 1024


def prepare_offer_photo(upload):
    if upload is None or not upload.filename:
        return None
    raw = upload.stream.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("写真は900 KB以下にしてください。")
    try:
        with Image.open(BytesIO(raw), formats=("JPEG", "PNG", "WEBP")) as source:
            if source.width * source.height > 20_000_000:
                raise ValueError("写真が大きすぎます。縮小してから添付してください。")
            if getattr(source, "is_animated", False):
                raise ValueError("動かない写真を1枚選んでください。")
            photo = ImageOps.exif_transpose(source)
            photo.thumbnail((960, 960), Image.Resampling.LANCZOS)
            if photo.mode not in {"RGB", "RGBA"}:
                photo = photo.convert("RGBA" if "transparency" in photo.info else "RGB")

            full, thumbnail = BytesIO(), BytesIO()
            photo.save(full, format="WEBP", quality=82)
            ImageOps.fit(photo, (96, 96), Image.Resampling.LANCZOS).save(
                thumbnail, format="WEBP", quality=80,
            )
            return full.getvalue(), thumbnail.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
        raise ValueError("JPEG・PNG・WebPの写真を選んでください。") from error
