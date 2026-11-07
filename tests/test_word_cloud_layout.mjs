import assert from "node:assert/strict";
import test from "node:test";
import { packWords } from "../modules/word_cloud/layout.mjs";

test("all labels fit without collisions at mobile, tablet and desktop widths", () => {
  for (const width of [280, 320, 560, 900, 1400]) {
    for (const count of [1, 10, 50, 80]) {
      const boxes = Array.from({ length: count }, (_, index) => ({ index,
        width: Math.min(width - 24, 40 + index * 37 % 220), height: 44 + index % 3 * 10 }));
      const packed = packWords(boxes, width);
      assert.equal(packed.boxes.length, count);
      for (const [i, box] of packed.boxes.entries()) {
        assert.ok(box.x >= 5 && box.y >= 5);
        assert.ok(box.x + box.width <= width - 5);
        assert.ok(box.y + box.height <= packed.height - 5);
        for (const other of packed.boxes.slice(i + 1)) {
          assert.ok(box.x + box.width + 10 <= other.x || other.x + other.width + 10 <= box.x ||
            box.y + box.height + 10 <= other.y || other.y + other.height + 10 <= box.y);
        }
      }
    }
  }
});

test("reset can rearrange a cloud while the same inputs remain stable", () => {
  const boxes = Array.from({ length: 15 }, (_, index) => ({ index, width: 100, height: 44 }));
  assert.deepEqual(packWords(boxes, 800), packWords(boxes, 800));
  assert.notDeepEqual(packWords(boxes, 800), packWords(boxes, 800, 430, 0.7));
});
