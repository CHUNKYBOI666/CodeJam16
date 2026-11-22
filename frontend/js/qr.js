import QRCode from "qrcode";

export function renderQr(canvas, url) {
  return QRCode.toCanvas(canvas, url, { width: 150, margin: 2, errorCorrectionLevel: "M" });
}
