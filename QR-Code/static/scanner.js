(() => {
  const startButton = document.getElementById("start-camera");
  if (!startButton) return;

  const stopButton = document.getElementById("stop-camera");
  const video = document.getElementById("camera-video");
  const status = document.getElementById("camera-status");
  const form = document.getElementById("camera-scan-form");
  const input = document.getElementById("qr-content");
  let stream = null;
  let detector = null;
  let scanning = false;

  function stopCamera() {
    scanning = false;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    video.srcObject = null;
    video.hidden = true;
    startButton.disabled = false;
    stopButton.disabled = true;
  }

  async function findCode() {
    if (!scanning) return;
    try {
      const codes = await detector.detect(video);
      if (codes.length && codes[0].rawValue) {
        input.value = codes[0].rawValue;
        status.textContent = "QR-Code erkannt. Die Daten werden ausgewertet.";
        stopCamera();
        form.requestSubmit();
        return;
      }
    } catch (_error) {
      status.textContent = "Das Kamerabild konnte nicht gelesen werden.";
      stopCamera();
      return;
    }
    window.setTimeout(findCode, 220);
  }

  startButton.addEventListener("click", async () => {
    if (!("BarcodeDetector" in window)) {
      status.textContent =
        "Dieser Browser unterstützt den Kamerascanner nicht. Nutze den Bild-Upload oder die manuelle Eingabe.";
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status.textContent =
        "Kamerazugriff ist nicht verfügbar. Verwende HTTPS oder den Bild-Upload.";
      return;
    }

    try {
      detector = new window.BarcodeDetector({ formats: ["qr_code"] });
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
      });
      video.srcObject = stream;
      video.hidden = false;
      await video.play();
      scanning = true;
      startButton.disabled = true;
      stopButton.disabled = false;
      status.textContent = "Kamera aktiv. Richte sie auf einen QR-Code.";
      findCode();
    } catch (_error) {
      stopCamera();
      status.textContent =
        "Die Kamera konnte nicht gestartet werden. Prüfe die Berechtigung oder nutze den Bild-Upload.";
    }
  });

  stopButton.addEventListener("click", () => {
    stopCamera();
    status.textContent = "Kamera gestoppt.";
  });

  window.addEventListener("pagehide", stopCamera);
})();
