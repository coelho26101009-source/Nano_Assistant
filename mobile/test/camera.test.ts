import { describe, expect, it } from "vitest";
import { base64Bytes, fitSize, jpegBase64, MAX_SIDE } from "../src/lib/camera";
import { closeTopOverlay, pushOverlay } from "../src/lib/overlay";

describe("camera helpers", () => {
  it("fits the longest side and never scales up", () => {
    expect(fitSize(4000, 3000)).toEqual({ width: MAX_SIDE, height: 768 });
    expect(fitSize(1080, 1920)).toEqual({ width: 576, height: MAX_SIDE });
    expect(fitSize(640, 480)).toEqual({ width: 640, height: 480 });
    expect(fitSize(0, 480)).toEqual({ width: 0, height: 0 });
  });

  it("takes only a JPEG data URL apart", () => {
    expect(jpegBase64("data:image/jpeg;base64,QUJD")).toBe("QUJD");
    expect(jpegBase64("data:image/png;base64,QUJD")).toBe("");
    expect(jpegBase64("data:,")).toBe("");
  });

  it("counts decoded bytes", () => {
    expect(base64Bytes("QUJD")).toBe(3);
    expect(base64Bytes("QUI=")).toBe(2);
    expect(base64Bytes("QQ==")).toBe(1);
  });
});

describe("back button layers", () => {
  it("closes the top layer first, and nothing once it is gone", () => {
    const closed: string[] = [];
    const offA = pushOverlay(() => closed.push("a"));
    pushOverlay(() => closed.push("b"));
    expect(closeTopOverlay()).toBe(true);
    offA();
    expect(closeTopOverlay()).toBe(false);
    expect(closed).toEqual(["b"]);
  });
});
