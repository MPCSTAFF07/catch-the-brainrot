const photoForm = document.getElementById("trade-post-form");

if (photoForm) {
  const photoField = photoForm.elements.photo;
  const status = document.getElementById("trade-photo-status");
  const limit = Number(photoField.dataset.photoLimit);
  let processing = false;

  async function resize(file) {
    if (typeof createImageBitmap !== "function" || typeof DataTransfer !== "function") {
      throw new Error("unsupported");
    }
    const bitmap = await createImageBitmap(file);
    try {
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      for (const edge of [960, 720, 480]) {
        const scale = Math.min(edge / Math.max(bitmap.width, bitmap.height), 1);
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.82));
        if (blob && blob.size <= limit) return new File([blob], "photo.jpg", { type: "image/jpeg" });
      }
      throw new Error("large");
    } finally {
      bitmap.close();
    }
  }

  photoField.addEventListener("change", () => {
    status.textContent = "JPEG・PNG・WebPを1枚添付できます。";
  });

  photoForm.addEventListener("submit", async event => {
    const file = photoField.files[0];
    if (processing) { event.preventDefault(); return; }
    if (!file || file.size <= limit) return;

    event.preventDefault();
    processing = true;
    status.textContent = "写真を縮小しています…";
    let ready = false;
    try {
      const compressed = await resize(file);
      if (photoField.files[0] !== file) return;
      const transfer = new DataTransfer();
      transfer.items.add(compressed);
      photoField.files = transfer.files;
      ready = true;
      status.textContent = "写真を添付して投稿します。";
    } catch {
      status.textContent = "この写真は縮小できませんでした。900 KB以下のJPEG・PNG・WebPを選び直してください。";
    } finally {
      processing = false;
    }
    if (ready) photoForm.requestSubmit();
  });
}
