import { describe, expect, it } from "vitest";
import {
  buildFallbackLayers,
  buildSelectionFilter,
  buildVectorLayers,
  createDiamondSdfImage,
  FACILITY_ICON_ID,
  isLayerZoomHidden,
  MAP_LAYER_MIN_ZOOM,
} from "../lib/mapStyle";

describe("mapStyle layer zoom thresholds and specs", () => {
  it("applies progressive minzooms across fields, facilities, labels, and wells", () => {
    expect(MAP_LAYER_MIN_ZOOM.fields).toBe(0);
    expect(MAP_LAYER_MIN_ZOOM.facilities).toBe(5);
    expect(MAP_LAYER_MIN_ZOOM.fieldLabels).toBe(6);
    expect(MAP_LAYER_MIN_ZOOM.wells).toBe(8);

    const byId = new Map(buildVectorLayers().map((layer) => [layer.id, layer]));
    expect(byId.get("fields-fill")?.minzoom).toBeUndefined();
    expect(byId.get("facilities-symbol")?.minzoom).toBe(MAP_LAYER_MIN_ZOOM.facilities);
    expect(byId.get("fields-label")?.minzoom).toBe(MAP_LAYER_MIN_ZOOM.fieldLabels);
    expect(byId.get("wells-circle")?.minzoom).toBe(MAP_LAYER_MIN_ZOOM.wells);

    const fallbackById = new Map(buildFallbackLayers().map((layer) => [layer.id, layer]));
    expect(fallbackById.get("facilities-symbol")?.minzoom).toBe(MAP_LAYER_MIN_ZOOM.facilities);
  });

  it("renders facilities as an overlapping SDF diamond symbol and labels fields with Noto Sans Regular", () => {
    const byId = new Map(buildVectorLayers().map((layer) => [layer.id, layer]));
    const fac = byId.get("facilities-symbol");
    expect(fac?.type).toBe("symbol");
    if (fac?.type === "symbol") {
      expect(fac.layout?.["icon-image"]).toBe(FACILITY_ICON_ID);
      expect(fac.layout?.["icon-allow-overlap"]).toBe(true);
      expect(fac.layout?.["icon-ignore-placement"]).toBe(true);
    }

    const labels = byId.get("fields-label");
    expect(labels?.type).toBe("symbol");
    if (labels?.type === "symbol") {
      expect(labels["source-layer"]).toBe("field_centroids");
      expect(labels.layout?.["text-font"]).toEqual(["Noto Sans Regular"]);
    }
  });

  it("tracks zoom-hidden state cleanly at each layer threshold", () => {
    expect(isLayerZoomHidden("facilities", 4.99)).toBe(true);
    expect(isLayerZoomHidden("facilities", 5)).toBe(false);
    expect(isLayerZoomHidden("wells", 7.99)).toBe(true);
    expect(isLayerZoomHidden("wells", 8)).toBe(false);
  });
});

describe("createDiamondSdfImage", () => {
  it("produces a symmetric MapLibre SDF with inside=255, boundary~191, and padded transparent corners", () => {
    const size = 48;
    const halfDiag = 14;
    const img = createDiamondSdfImage(size, halfDiag);
    expect(img.width).toBe(size);
    expect(img.height).toBe(size);
    expect(img.data).toHaveLength(size * size * 4);

    const alphaAt = (x: number, y: number) => img.data[(y * size + x) * 4 + 3];

    // Center is deep inside; outer corners are well outside the 8px SDF falloff.
    expect(alphaAt(size / 2, size / 2)).toBe(255);
    expect(alphaAt(0, 0)).toBe(0);
    expect(alphaAt(size - 1, size - 1)).toBe(0);

    // Right-tip boundary (ax=13.5, ay=0.5 => ax+ay=14) hits MapLibre's 0.75 edge (191),
    // with strictly higher alpha inside and lower alpha outside.
    expect(alphaAt(size / 2 + halfDiag - 2, size / 2)).toBeGreaterThan(191);
    expect(alphaAt(size / 2 + halfDiag - 1, size / 2)).toBe(191);
    expect(alphaAt(size / 2 + halfDiag, size / 2)).toBeLessThan(191);

    // 4-fold symmetry across axes and transpose.
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const a = alphaAt(x, y);
        expect(alphaAt(size - 1 - x, y)).toBe(a);
        expect(alphaAt(x, size - 1 - y)).toBe(a);
        expect(alphaAt(y, x)).toBe(a);
      }
    }
  });
});

describe("buildSelectionFilter", () => {
  it("matches only the selected entity kind and respects activeSource", () => {
    const facSel = { kind: "facility" as const, source: "brazil", id: "FPSO-1" };
    expect(buildSelectionFilter("facility", facSel, null)).toEqual([
      "all",
      ["==", ["get", "source"], "brazil"],
      ["==", ["get", "facility_id"], "FPSO-1"],
    ]);
    expect(buildSelectionFilter("field", facSel, null)).toEqual([
      "==",
      ["get", "source"],
      "__none__",
    ]);
    expect(buildSelectionFilter("facility", facSel, "norway")).toEqual([
      "==",
      ["get", "source"],
      "__none__",
    ]);
  });
});
