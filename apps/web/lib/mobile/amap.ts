/** Browser-only AMap loader and the small API surface used by mobile maps. */

export type MapCoordinateSystem = "gcj02" | "wgs84" | "unknown";

export interface MapEndpoint {
  id: string;
  label: string;
  latitude: number;
  longitude: number;
  coordinateSystem?: MapCoordinateSystem | null;
}

export interface AMapMarker {
  on(event: "click", handler: () => void): void;
  setMap(map: AMapMap | null): void;
}

export interface AMapPolyline {
  setMap(map: AMapMap | null): void;
}

export interface AMapLngLat {
  getLat(): number;
  getLng(): number;
}

export interface AMapMap {
  add(overlays: Array<AMapMarker | AMapPolyline>): void;
  destroy(): void;
  getCenter(): AMapLngLat;
  off(event: "moveend", handler: () => void): void;
  on(event: "moveend", handler: () => void): void;
  setFitView(
    overlays?: Array<AMapMarker | AMapPolyline>,
    immediately?: boolean,
    avoid?: number[],
    maxZoom?: number,
  ): void;
  setZoomAndCenter(zoom: number, center: [number, number]): void;
}

export interface AMapNamespace {
  Map: new (
    container: HTMLElement,
    options?: {
      center?: [number, number];
      zoom?: number;
      viewMode?: "2D" | "3D";
    },
  ) => AMapMap;
  Marker: new (options: {
    position: [number, number];
    title?: string;
    anchor?: string;
    content?: string;
    zIndex?: number;
  }) => AMapMarker;
  Polyline: new (options: {
    path: Array<[number, number]>;
    strokeColor?: string;
    strokeWeight?: number;
    strokeOpacity?: number;
    strokeStyle?: "solid" | "dashed";
    strokeDasharray?: number[];
    lineJoin?: "round" | "miter" | "bevel";
    lineCap?: "round" | "butt" | "square";
    showDir?: boolean;
    zIndex?: number;
  }) => AMapPolyline;
}

declare global {
  interface Window {
    AMap?: AMapNamespace;
    _AMapSecurityConfig?: {
      securityJsCode?: string;
      serviceHost?: string;
    };
    __superappAmapLoader?: Promise<AMapNamespace>;
  }
}

const SCRIPT_ID = "superapp-amap-jsapi";

/** Read the public browser map settings without exposing them in error text. */
function amapConfig(): { key: string; securityJsCode: string } {
  const key = process.env.NEXT_PUBLIC_AMAP_JS_KEY?.trim() ?? "";
  const securityJsCode =
    process.env.NEXT_PUBLIC_AMAP_SECURITY_CODE?.trim() ?? "";
  if (!key) {
    throw new Error(
      "地图配置缺失：需要 Web 端 JS API Key（NEXT_PUBLIC_AMAP_JS_KEY）。",
    );
  }
  if (!securityJsCode) {
    throw new Error(
      "地图配置缺失：需要与该 Key 配套的 Web 端安全密钥（NEXT_PUBLIC_AMAP_SECURITY_CODE）。",
    );
  }
  return { key, securityJsCode };
}

/**
 * Load AMap 2.0 once from the official HTTPS endpoint.
 *
 * The local development build uses the plaintext security key flow. Both
 * NEXT_PUBLIC values are visible to browser users and must not be treated as
 * server secrets.
 */
export function loadAmap(): Promise<AMapNamespace> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("地图只能在浏览器中加载。"));
  }
  if (window.AMap) return Promise.resolve(window.AMap);
  if (window.__superappAmapLoader) return window.__superappAmapLoader;

  let config: { key: string; securityJsCode: string };
  try {
    config = amapConfig();
  } catch (caught) {
    return Promise.reject(caught);
  }
  const { key, securityJsCode } = config;
  window._AMapSecurityConfig = { securityJsCode };

  const promise = new Promise<AMapNamespace>((resolve, reject) => {
    const existing = document.getElementById(SCRIPT_ID) as
      | HTMLScriptElement
      | null;
    const script = existing ?? document.createElement("script");
    let timeoutId: number | null = null;

    const cleanup = () => {
      if (timeoutId !== null) window.clearTimeout(timeoutId);
      script.removeEventListener("load", onLoad);
      script.removeEventListener("error", onError);
    };
    const onLoad = () => {
      cleanup();
      if (window.AMap) {
        resolve(window.AMap);
      } else {
        window.__superappAmapLoader = undefined;
        reject(new Error("高德地图脚本已返回，但地图对象没有初始化。"));
      }
    };
    const onError = () => {
      cleanup();
      window.__superappAmapLoader = undefined;
      script.remove();
      reject(new Error("高德地图资源加载失败，请检查网络或 Web 端地图配置。"));
    };

    script.addEventListener("load", onLoad, { once: true });
    script.addEventListener("error", onError, { once: true });
    timeoutId = window.setTimeout(() => {
      cleanup();
      window.__superappAmapLoader = undefined;
      reject(new Error("高德地图加载超时，请稍后重试。"));
    }, 15000);

    if (!existing) {
      script.id = SCRIPT_ID;
      script.async = true;
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}`;
      document.head.appendChild(script);
    }
  });

  window.__superappAmapLoader = promise;
  promise.catch(() => {
    if (window.__superappAmapLoader === promise) {
      window.__superappAmapLoader = undefined;
    }
  });
  return promise;
}
