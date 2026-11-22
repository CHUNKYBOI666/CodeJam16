const mapVersion = "kitchen-v2";

export function kitchenLocation(check) {
  const { map_version: version, map_x: x, map_y: y } = check;
  if (version !== mapVersion || ![x, y].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) {
    return null;
  }
  return { x, y };
}

export function renderKitchenMap(check) {
  const location = kitchenLocation(check);
  if (!location) return null;

  const figure = document.createElement("figure");
  figure.className = "kitchen-map";
  figure.setAttribute("role", "img");
  figure.setAttribute("aria-label",
    `Floor minimap. Person approximately ${Math.round(location.x * 100)}% across and ${Math.round(location.y * 100)}% down from the upper-left corner.`);
  const map = document.createElement("div");
  map.className = "kitchen-map-floor";
  map.setAttribute("aria-hidden", "true");
  const wall = document.createElement("span");
  wall.className = "kitchen-map-wall";
  const chair = document.createElement("span");
  chair.className = "kitchen-map-chair";
  const pin = document.createElement("span");
  pin.className = "kitchen-map-pin";
  pin.style.left = location.x * 100 + "%";
  pin.style.top = location.y * 100 + "%";
  map.append(wall, chair, pin);
  figure.append(map);
  return figure;
}
