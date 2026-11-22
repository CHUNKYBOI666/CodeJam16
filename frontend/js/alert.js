import { renderKitchenMap } from "./kitchen-map.js";

let audio;

export function unlockAudio() {
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (!Audio) return;
  audio ??= new Audio();
  void audio.resume().catch(() => {});
}

function playAlert() {
  if (audio?.state !== "running") return false;
  const start = audio.currentTime;
  for (const [offset, frequency] of [[0, 660], [0.22, 880]]) {
    const tone = audio.createOscillator();
    const volume = audio.createGain();
    tone.frequency.value = frequency;
    volume.gain.setValueAtTime(0.001, start + offset);
    volume.gain.exponentialRampToValueAtTime(0.16, start + offset + 0.02);
    volume.gain.exponentialRampToValueAtTime(0.001, start + offset + 0.18);
    tone.connect(volume).connect(audio.destination);
    tone.start(start + offset);
    tone.stop(start + offset + 0.2);
  }
  return true;
}

export function createTakeover({ dialog, place, age, source, map, accept, dismiss }, onAccept, onDismiss) {
  let activeId = null;
  const sounded = new Set();
  const vibrated = new Set();
  accept.addEventListener("click", () => onAccept(activeId));
  dismiss.addEventListener("click", () => onDismiss(activeId));
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    onDismiss(activeId);
  });
  return (check, elapsed, online, pending) => {
    if (!check) {
      activeId = null;
      map.replaceChildren();
      if (dialog.open) dialog.close();
      return;
    }
    activeId = check.id;
    const minimap = renderKitchenMap(check);
    place.hidden = Boolean(minimap);
    place.textContent = minimap ? "" : check.place;
    age.textContent = elapsed;
    source.textContent = check.source === "mock" ? "Mock check" : check.source === "replay" ? "Rehearsal clip" : "";
    source.hidden = !source.textContent;
    map.replaceChildren(...(minimap ? [minimap] : []));
    accept.disabled = !online || pending;
    if (!dialog.open) dialog.showModal();
    if (!sounded.has(activeId) && playAlert()) sounded.add(activeId);
    if (!vibrated.has(activeId) && navigator.vibrate?.([140, 80, 140])) vibrated.add(activeId);
  };
}
