export type FertilityStatus = "Good" | "Moderate" | "Poor";
export type NutrientStatus = "deficient" | "ample" | "adequate";

export interface Recommendation {
  element: string;
  status: NutrientStatus;
  deficit: number;
  value: number;
  ideal_range: string;
  solution: string;
  source: "rule-based" | "llm";
}

export interface CropMatch {
  crop: string;
  scientific_name: string | null;
  confidence: number; // 0-100
  temperature: number; // °C median for this crop (real dataset)
  rainfall: number; // mm median for this crop (real dataset)
}

export interface PredictResponse {
  fertility_status: FertilityStatus;
  confidence: number; // 0-100, predicted-class probability
  recommendations: Recommendation[];
  crops: CropMatch[];
}

export interface CropInfo {
  name: string;
  scientific_name: string | null;
  bands: Record<"N" | "P" | "K" | "ph", [number, number]>;
  temperature: number;
  rainfall: number;
}

export interface SoilValues {
  N: number;
  P: number;
  K: number;
  ph: number;
}

export interface ClimateValues {
  temperature: number;
  humidity: number;
  rainfall: number;
}

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new Error(
      `Cannot reach the analysis API at ${API_BASE}. Make sure it is running (uvicorn api:app --port 8000) and that its CORS settings allow this origin.`,
    );
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body.detail) detail = String(body.detail);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

/** Lightweight reachability probe — no custom headers, so no CORS preflight. */
export async function checkHealth(): Promise<boolean> {
  try {
    const resp = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
    return resp.ok;
  } catch {
    return false;
  }
}

export function predict(
  values: SoilValues,
  crop: string | null,
  climate: ClimateValues,
  lang: string = "en",
): Promise<PredictResponse> {
  return request<PredictResponse>("/api/predict", {
    method: "POST",
    body: JSON.stringify({ ...values, crop, ...climate, lang }),
  });
}

export function fetchCrops(): Promise<{ crops: CropInfo[] }> {
  return request<{ crops: CropInfo[] }>("/api/crops");
}
