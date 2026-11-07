// Pack measured labels with room for their six-pixel floating motion.
export function packWords(boxes, width, initialHeight = 430, seed = 0) {
  const gap = 10;
  const fits = (box, placed, height) => box.x >= gap && box.y >= gap &&
    box.x + box.width <= width - gap && box.y + box.height <= height - gap &&
    placed.every(other => box.x + box.width + gap <= other.x ||
      other.x + other.width + gap <= box.x || box.y + box.height + gap <= other.y ||
      other.y + other.height + gap <= box.y);
  const area = boxes.reduce((sum, box) => sum + (box.width + gap) * (box.height + gap), 0);
  let height = Math.max(initialHeight, Math.ceil(area / Math.max(1, width - 20) * 1.65));
  for (let attempt = 0; attempt < 5; attempt++, height = Math.ceil(height * 1.22)) {
    const placed = [];
    for (const box of boxes) {
      let found = false;
      // Try the edges of existing labels first. This fills small gaps on phones
      // instead of stretching a narrow spiral into a long column of words.
      const candidates = [{ ...box, x: (width - box.width) / 2, y: (height - box.height) / 2 }];
      for (const other of placed) {
        for (const x of [other.x, other.x + other.width - box.width, other.x + (other.width - box.width) / 2]) {
          candidates.push({ ...box, x, y: other.y - gap - box.height },
            { ...box, x, y: other.y + other.height + gap });
        }
        for (const y of [other.y, other.y + other.height - box.height, other.y + (other.height - box.height) / 2]) {
          candidates.push({ ...box, x: other.x - gap - box.width, y },
            { ...box, x: other.x + other.width + gap, y });
        }
      }
      const score = candidate => {
        const dx = candidate.x + box.width / 2 - width / 2;
        const dy = (candidate.y + box.height / 2 - height / 2) * Math.max(1, width / height);
        return (dx * dx + dy * dy) * (1 + 0.12 * Math.sin(Math.atan2(dy, dx) * 3 + seed));
      };
      candidates.sort((a, b) => score(a) - score(b));
      for (const candidate of candidates) {
        if (fits(candidate, placed, height)) {
          placed.push(candidate);
          found = true;
          break;
        }
      }
      if (found) continue;
      for (let step = 0; step < 1800; step++) {
        const angle = step * 2.3999632297 + seed;
        const radius = Math.sqrt(step / 1800) * 0.72;
        const candidate = { ...box,
          x: Math.round(width / 2 + Math.cos(angle) * width * radius - box.width / 2),
          y: Math.round(height / 2 + Math.sin(angle) * height * radius - box.height / 2) };
        if (fits(candidate, placed, height)) {
          placed.push(candidate);
          found = true;
          break;
        }
      }
      if (!found) break;
    }
    if (placed.length === boxes.length) return { boxes: placed, height };
  }
  // Extremely long labels still get a complete, readable layout.
  const placed = [];
  let x = gap, y = gap, rowHeight = 0;
  for (const box of boxes) {
    if (x + box.width > width - gap && x > gap) {
      x = gap;
      y += rowHeight + gap;
      rowHeight = 0;
    }
    placed.push({ ...box, x, y });
    x += box.width + gap;
    rowHeight = Math.max(rowHeight, box.height);
  }
  return { boxes: placed, height: Math.max(initialHeight, y + rowHeight + gap) };
}
