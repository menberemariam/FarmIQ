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

export type SuitabilityClass = "S1" | "S2" | "S3" | "N";

export interface Suitability {
  class: SuitabilityClass;
  reasons: string[];
}

export interface CropMatch {
  crop: string;
  scientific_name: string | null;
  confidence: number; // 0-100
  temperature: number; // °C median for this crop (real dataset)
  rainfall: number; // mm median for this crop (real dataset)
  // additive: present only when the request included temperature+rainfall
  suitability?: Suitability;
}

export interface PredictWarning {
  field: string;
  value: number;
  range: [number, number];
  message: string;
}

export interface AgroecologyBlock {
  zone: string;
  name: string;
  bands: Record<"N" | "P" | "K" | "ph", [number, number]>;
  constraint: string;
  source: string;
  salinity?: {
    ec_e_ds_m: number;
    pause_threshold_ds_m: number;
    fertilizer_paused: boolean;
    esp: number | null;
    sodic_threshold_pct: number | null;
    sodic: boolean;
    reclamation_product: string | null;
  };
}

export type SoilTypeId =
  | "nitisol"
  | "vertisol"
  | "cambisol_luvisol"
  | "andosol"
  | "arenosol";

export type LimeType = "calcitic" | "dolomitic";

export interface LimeScheduleEntry {
  season: number;
  t_ha: number;
}

export interface LimePlan {
  soil_type: SoilTypeId;
  name_en: string;
  name_am: string;
  name_om: string;
  lrf: number; // t/ha per 1.0 pH unit at ecce_pct
  ecce_pct: number;
  lime_type: LimeType;
  ph_deficit: number;
  total_t_ha: number; // total program across seasons
  max_single_t_ha: number; // 5.0 Ethiopian ceiling
  split_required: boolean;
  schedule: LimeScheduleEntry[];
  source: string;
  lang?: string;
}

export interface VarietyBlock {
  id: string;
  crop: string;
  crop_label: string | null;
  region: string | null;
  region_label: string | null;
  label: string;
  aliases: string[];
  bands: Record<"N" | "P" | "K" | "ph", [number, number]>;
  source: string | null;
  review_status: string | null;
  match_level: string;
}

export interface VarietyEntry {
  id: string;
  crop: string;
  crop_label: string;
  region: string;
  region_label: string;
  variety: string;
  aliases: string[];
  bands: Record<"N" | "P" | "K" | "ph", [number, number]>;
  source: string;
  review_status: string;
}

export interface SeasonBlock {
  id: string;
  crop: string;
  crop_label: string;
  season: "meher" | "belg";
  label: string;
  months: string | null;
  bands: Record<"N" | "P" | "K" | "ph", [number, number]>;
  n_band: [number, number];
  base_bands: Record<"P" | "K" | "ph", [number, number]>;
  n_factor: number | null;
  applied_n_source: string | null;
  unchanged: string[];
  note: string;
  season_note: string;
  source: string;
  review_status: string;
}

export interface SeasonEntry {
  id: "meher" | "belg";
  label: string;
  months: string | null;
  note: string | null;
}

export type TextureClassId = "sandy" | "loam" | "clay";
export type SocClassId = "very_low" | "low" | "medium" | "high";

export interface SoilConditioningInput {
  texture_class?: string | null;
  soc_pct?: number | null;
  soc_class?: string | null;
  woreda?: string | null;
}

export interface ConditioningRuleApplied {
  col: "N" | "P" | "K" | "ph";
  rule: string;
  key: string;
  delta: [number, number];
  before: [number, number];
  after: [number, number];
}

export interface SoilConditioningBlock {
  texture_class: string | null;
  soc_class: string | null;
  soc_pct: number | null;
  woreda: string | null;
  source_level: { texture: string | null; soc: string | null };
  applied: ConditioningRuleApplied[];
  base_bands: Record<"N" | "P" | "K" | "ph", [number, number]>;
  adjusted_bands: Record<"N" | "P" | "K" | "ph", [number, number]>;
  source: string | null;
  review_status: string | null;
  lang?: string;
}

export interface SoilConditioningReference {
  meta: Record<string, unknown>;
  texture_classes: {
    id: TextureClassId;
    name_en: string;
    aliases: string[];
    retention: string | null;
  }[];
  soc_classes: { id: SocClassId; name_en: string; min_pct: number | null; max_pct: number | null }[];
  rules: Record<string, unknown>;
  defaults: Record<string, unknown>;
  woreda_count: number;
  enabled: boolean;
  use_soil_type: boolean;
}

export interface PredictResponse {
  fertility_status: FertilityStatus;
  confidence: number; // 0-100, predicted-class probability
  input_units?: "kg_ha" | "lab"; // unit system the request arrived in
  recommendations: Recommendation[];
  crops: CropMatch[];
  warnings?: PredictWarning[]; // inputs outside the model's training range
  llm_used?: boolean; // any recommendation was LLM-enriched and validated
  model_version?: string;
  agroecology?: AgroecologyBlock; // present only when a zone was requested
  lime_plan?: LimePlan; // present only when soil_type was requested (and pH deficient)
  variety?: VarietyBlock; // present only when a named variety was used
  season?: SeasonBlock; // present only when a supported season was used
  soil_conditioning?: SoilConditioningBlock; // present only with E inputs
}

export interface CropInfo {
  name: string;
  scientific_name: string | null;
  bands: Record<"N" | "P" | "K" | "ph", [number, number]>;
  // additive: provenance of the percentile bands (non-Ethiopian baseline)
  bands_source?: string;
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

export interface HealthInfo {
  status: string;
  models: string;
  model_version?: string;
  // additive: true only when the deployment captures consented, unlabeled
  // training samples (TRAINING_DATA_CAPTURE). Off by default.
  training_capture?: boolean;
}

/** Health payload; null when the API cannot be reached. */
export async function fetchHealth(): Promise<HealthInfo | null> {
  try {
    const resp = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
    if (!resp.ok) return null;
    return (await resp.json()) as HealthInfo;
  } catch {
    return null;
  }
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

export interface PredictRequestPayload {
  N: number;
  P: number;
  K: number;
  ph: number;
  crop: string | null;
  temperature: number;
  humidity: number;
  rainfall: number;
  lang: string;
  use_llm: boolean;
  agroecological_zone?: string;
  soil_type?: string;
  lime_type?: LimeType;
  variety?: string;
  region?: string;
  season?: string;
  texture_class?: string;
  soc_pct?: number;
  soc_class?: string;
  woreda?: string;
}

/** Shared payload assembly so /api/predict and its SSE variant stay identical. */
export function buildPredictPayload(
  values: SoilValues,
  crop: string | null,
  climate: ClimateValues,
  lang: string = "en",
  useLlm: boolean = true,
  zone: string | null = null,
  soilType: string | null = null,
  limeType: LimeType = "calcitic",
  variety: string | null = null,
  region: string | null = null,
  season: string | null = null,
  conditioning: SoilConditioningInput = {},
): PredictRequestPayload {
  return {
    ...values,
    crop,
    ...climate,
    lang,
    use_llm: useLlm,
    // Additive only: no key at all when no zone is selected, so the
    // legacy request/response shape stays byte-identical.
    ...(zone ? { agroecological_zone: zone } : {}),
    // Ethiopian LRF lime requirement — additive; without soil_type the
    // legacy lime formula applies and no lime_plan block is returned.
    ...(soilType ? { soil_type: soilType } : {}),
    ...(soilType ? { lime_type: limeType } : {}),
    // Enhancement C: explicit variety bands. Omitted entirely unless the
    // user selects a named variety, so legacy requests stay identical.
    ...(variety ? { variety } : {}),
    ...(variety && region ? { region } : {}),
    // Enhancement D: seasonal N-only modifier, omitted when not selected.
    ...(season ? { season } : {}),
    // Enhancement E: optional conditioning, omitted entirely when unset so
    // legacy requests stay byte-identical.
    ...(conditioning.texture_class
      ? { texture_class: conditioning.texture_class }
      : {}),
    ...(conditioning.soc_pct != null ? { soc_pct: conditioning.soc_pct } : {}),
    ...(conditioning.soc_class
      ? { soc_class: conditioning.soc_class }
      : {}),
    ...(conditioning.woreda ? { woreda: conditioning.woreda } : {}),
  };
}

export function predict(
  values: SoilValues,
  crop: string | null,
  climate: ClimateValues,
  lang: string = "en",
  useLlm: boolean = true,
  zone: string | null = null,
  soilType: string | null = null,
  limeType: LimeType = "calcitic",
  variety: string | null = null,
  region: string | null = null,
  season: string | null = null,
  conditioning: SoilConditioningInput = {},
): Promise<PredictResponse> {
  return request<PredictResponse>("/api/predict", {
    method: "POST",
    body: JSON.stringify(buildPredictPayload(
      values, crop, climate, lang, useLlm, zone, soilType, limeType,
      variety, region, season, conditioning,
    )),
  });
}

export interface StreamDone {
  recommendations: Recommendation[];
  llm_used: boolean;
  validated: string[];
  rejected: string[];
  truncated: boolean;
  reason: string | null;
}

export interface PredictStreamHandlers {
  onSnapshot?: (snapshot: PredictResponse) => void;
  onElement?: (recommendation: Recommendation) => void;
  onDone?: (done: StreamDone) => void;
}

function handleSseBlock(block: string, handlers: PredictStreamHandlers) {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (data.length === 0) return;

  let parsed: unknown;
  try {
    parsed = JSON.parse(data.join("\n"));
  } catch {
    return;
  }

  if (event === "snapshot") {
    handlers.onSnapshot?.(parsed as PredictResponse);
  } else if (event === "element") {
    handlers.onElement?.(parsed as Recommendation);
  } else if (event === "done") {
    handlers.onDone?.(parsed as StreamDone);
  }
  // "error" is followed by a deterministic "done" event, so it needs no
  // separate UI state: the final payload already carries the fallback.
}

/**
 * POST the same payload as predict() and consume its SSE response.
 *
 * The browser EventSource cannot POST, so this reads the body with a
 * ReadableStream. Event names are handled explicitly; partial JSON is never
 * parsed because the server only sends complete, validated recommendation
 * objects. Throws on transport/HTTP failures so the caller can fall back to
 * predict().
 */
export async function predictStream(
  payload: PredictRequestPayload,
  handlers: PredictStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}/api/predict/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") throw err;
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

  const contentType = resp.headers.get("content-type") ?? "";
  // FastAPI appends "; charset=utf-8"; never use strict equality here.
  if (!contentType.startsWith("text/event-stream") || !resp.body) {
    throw new Error("The analysis API did not return an event stream.");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) handleSseBlock(block, handlers);
  }

  buffer += decoder.decode();
  buffer = buffer.replace(/\r\n/g, "\n");
  if (buffer.trim()) handleSseBlock(buffer, handlers);
}

export function fetchCrops(): Promise<{ crops: CropInfo[] }> {
  return request<{ crops: CropInfo[] }>("/api/crops");
}

export interface SeasonalReference {
  seasons: SeasonEntry[];
  crops: string[];
}

export function fetchSeasons(): Promise<SeasonalReference> {
  return request<SeasonalReference>("/api/seasons");
}

export function fetchSoilConditioning(): Promise<SoilConditioningReference> {
  return request<SoilConditioningReference>("/api/soil-conditioning");
}

export function fetchVarieties(
  crop?: string | null,
  region?: string | null,
): Promise<{ varieties: VarietyEntry[] }> {
  const params = new URLSearchParams();
  if (crop) params.set("crop", crop);
  if (region) params.set("region", region);
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<{ varieties: VarietyEntry[] }>(`/api/varieties${query}`);
}

/* ---------------- Ethiopian crop matrix + suitability (Phase 5) -------------- */

export interface MatrixBands {
  ph: { min?: number; max?: number; lt?: number; gt?: number }[];
  temperature: { min?: number; max?: number; lt?: number; gt?: number }[];
  rainfall: { min?: number; max?: number; lt?: number; gt?: number }[];
}

export interface EthiopianCrop {
  name: string;
  name_en: string;
  name_am: string;
  name_om: string;
  category: string;
  in_ml_model: boolean;
  regions: string;
  S1: MatrixBands;
  S2: MatrixBands;
  N: MatrixBands;
}

export interface SuitabilityCheck {
  param: string;
  value: number;
  class: SuitabilityClass;
  band: string;
}

export interface SuitabilityResult {
  crop: string;
  class: SuitabilityClass;
  param_class: Record<string, SuitabilityClass>;
  checks: SuitabilityCheck[];
  reasons: string[];
}

export function fetchEthiopianCrops(): Promise<{ crops: EthiopianCrop[] }> {
  return request<{ crops: EthiopianCrop[] }>("/api/crops/ethiopian");
}

export function fetchSuitability(input: {
  ph?: number;
  temperature?: number;
  rainfall?: number;
  crops?: string[];
}): Promise<{ results: SuitabilityResult[] }> {
  return request<{ results: SuitabilityResult[] }>("/api/crops/suitability", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/* ---------------- Farmers / plots / outcomes (agent flow) ---------------- */

export type ReportSource = "manual" | "lab" | "iot_simulator" | "iot_device";

export interface Farmer {
  id: number;
  name: string;
  phone: string | null;
  region: string | null;
  lang: string;
  created_at: string;
  // present on list summaries only
  plot_count?: number;
  report_count?: number;
  last_report_at?: string | null;
}

export interface Plot {
  id: number;
  farmer_id: number;
  name: string;
  farm_size_ha: number | null;
  latitude: number | null;
  longitude: number | null;
  previous_crop: string | null;
  created_at: string;
  // present on farmer detail lists only
  report_count?: number;
  last_report_at?: string | null;
  latest_status?: FertilityStatus | null;
}

export interface Outcome {
  id: number;
  report_id: number;
  implemented_at: string | null;
  implementation_notes: string | null;
  expected_yield: number | null;
  actual_yield: number | null;
  yield_unit: string;
  harvest_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Report {
  id: number;
  plot: string;
  created_at: string;
  N: number;
  P: number;
  K: number;
  ph: number;
  crop: string | null;
  temperature: number | null;
  humidity: number | null;
  rainfall: number | null;
  fertility_status: FertilityStatus;
  recommendations: Recommendation[];
  crops: CropMatch[] | null;
  variety: string | null;
  season: string | null;
  region: string | null;
  texture_class: TextureClassId | null;
  soc_pct: number | null;
  soc_class: SocClassId | null;
  woreda: string | null;
  lang: string;
  farmer_id: number | null;
  plot_id: number | null;
  source: ReportSource;
  outcome?: Outcome | null;
}

export interface FarmerDetail {
  farmer: Farmer;
  plots: Plot[];
  reports: Report[];
}

export function fetchFarmers(): Promise<{ farmers: Farmer[] }> {
  return request<{ farmers: Farmer[] }>("/api/farmers");
}

export function fetchFarmer(farmerId: number): Promise<FarmerDetail> {
  return request<FarmerDetail>(`/api/farmers/${farmerId}`);
}

export function createFarmer(input: {
  name: string;
  phone?: string | null;
  region?: string | null;
  lang?: string;
}): Promise<{ id: number; farmer: Farmer }> {
  return request<{ id: number; farmer: Farmer }>("/api/farmers", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createPlot(input: {
  farmer_id: number;
  name: string;
  farm_size_ha?: number | null;
  latitude?: number | null;
  longitude?: number | null;
  previous_crop?: string | null;
}): Promise<{ id: number; plot: Plot }> {
  return request<{ id: number; plot: Plot }>("/api/plots", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function saveReportWithLinks(input: {
  report: SoilValues;
  plot: string;
  fertility_status: FertilityStatus;
  recommendations: Recommendation[];
  crop: string | null;
  climate: ClimateValues;
  crops: CropMatch[];
  lang: string;
  farmer_id?: number | null;
  plot_id?: number | null;
  agroecological_zone?: string | null;
  variety?: string | null;
  season?: string | null;
  region?: string | null;
  conditioning?: SoilConditioningInput;
  // Opt-in only: omitted entirely unless the user ticked the consent box, so
  // the legacy save payload stays byte-identical.
  consent_for_training?: boolean;
}): Promise<{ id: number; saved: boolean }> {
  return request<{ id: number; saved: boolean }>("/api/reports", {
    method: "POST",
    body: JSON.stringify({
      plot: input.plot,
      ...input.report,
      fertility_status: input.fertility_status,
      recommendations: input.recommendations,
      crop: input.crop,
      ...input.climate,
      crops: input.crops,
      lang: input.lang,
      farmer_id: input.farmer_id ?? null,
      plot_id: input.plot_id ?? null,
      source: "manual",
      ...(input.agroecological_zone
        ? { agroecological_zone: input.agroecological_zone }
        : {}),
      ...(input.variety ? { variety: input.variety } : {}),
      ...(input.season ? { season: input.season } : {}),
      ...(input.variety && input.region ? { region: input.region } : {}),
      ...(input.conditioning?.texture_class
        ? { texture_class: input.conditioning.texture_class }
        : {}),
      ...(input.conditioning?.soc_pct != null
        ? { soc_pct: input.conditioning.soc_pct }
        : {}),
      ...(input.conditioning?.soc_class
        ? { soc_class: input.conditioning.soc_class }
        : {}),
      ...(input.conditioning?.woreda
        ? { woreda: input.conditioning.woreda }
        : {}),
      ...(input.consent_for_training ? { consent_for_training: true } : {}),
    }),
  });
}

export function fetchReports(farmerId?: number): Promise<{ reports: Report[] }> {
  const query = farmerId ? `?farmer_id=${farmerId}` : "";
  return request<{ reports: Report[] }>(`/api/reports${query}`);
}

export function saveOutcome(
  reportId: number,
  input: {
    implementation_notes?: string | null;
    implemented_at?: string | null;
    expected_yield?: number | null;
    actual_yield?: number | null;
    yield_unit?: string;
    harvest_at?: string | null;
  },
): Promise<{ id: number; saved: boolean; outcome: Outcome }> {
  return request<{ id: number; saved: boolean; outcome: Outcome }>(
    `/api/reports/${reportId}/outcome`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function fetchOutcome(reportId: number): Promise<{ outcome: Outcome | null }> {
  return request<{ outcome: Outcome | null }>(`/api/reports/${reportId}/outcome`);
}

/* ---------------- Report detail + fertilizer cost estimate (Phase 4) ---------- */

export interface ReportDetail {
  report: Report;
  outcome: Outcome | null;
  farmer: Farmer | null;
  plot: Plot | null;
}

export interface CostLine {
  element: string;
  col: "N" | "P" | "K" | "ph";
  status: NutrientStatus;
  deficit: number;
  product: string | null;
  product_label: string | null;
  amount: number;
  unit: string;
  price_etb_per_kg: number;
  subtotal: number;
  purchase: boolean;
}

export interface CostEstimate {
  currency: string;
  lines: CostLine[];
  total_per_ha: number;
  size_ha: number | null;
  total_for_plot: number | null;
  crop: string | null;
  disclaimer: string;
  disclaimer_key: string;
}

export function fetchReportDetail(reportId: number): Promise<ReportDetail> {
  return request<ReportDetail>(`/api/reports/${reportId}`);
}

export function fetchCostEstimate(reportId: number): Promise<CostEstimate> {
  return request<CostEstimate>(`/api/reports/${reportId}/cost-estimate`);
}

/* ---------------- IoT: devices, readings, live simulation ---------------- */

export type IotDeviceStatus = "online" | "offline" | "never_seen";
export type ReadingQuality = "valid" | "warning";

export interface IotMeasurements {
  ph: number;
  nitrogen: number;
  phosphorus: number;
  potassium: number;
  moisture: number;
  temperature: number;
}

export interface IotReading {
  id: number;
  device: number; // device row id
  source: string; // "iot_simulator" for simulated traffic
  N: number;
  P: number;
  K: number;
  ph: number;
  moisture: number;
  temperature: number;
  latitude: number | null;
  longitude: number | null;
  quality: ReadingQuality;
  flags: string[]; // e.g. STALE_TIMESTAMP, SUDDEN_VALUE_CHANGE
  reading_ts: string;
  received_at: string;
}

export interface IotDevice {
  id: number;
  device_id: string;
  name: string;
  plot_id: number;
  sensors: string[] | null;
  firmware: string;
  battery: number | null; // simulated
  signal: number | null; // simulated dBm
  last_seen_at: string | null;
  created_at: string;
  // derived
  status: IotDeviceStatus;
  seconds_since_last: number | null;
  plot_name: string | null;
  farmer_id: number | null;
  latest_reading: IotReading | null;
}

export interface DeviceAssessment extends PredictResponse {
  device_id: string;
  reading_id: number;
  reading_ts: string;
  source: string;
  simulated: boolean;
}

export type SimulationScenario =
  | "normal"
  | "nitrogen_deficient"
  | "acidic"
  | "dry";

export interface SimulationState {
  running: boolean;
  scenario: SimulationScenario;
  interval_seconds: number;
  device_id: string | null;
  ticks: number;
  last_tick_at: string | null;
}

export function fetchDevices(): Promise<{ devices: IotDevice[] }> {
  return request<{ devices: IotDevice[] }>("/api/iot/devices");
}

export function fetchReadings(
  deviceId: string,
  limit: number = 30,
): Promise<{ readings: IotReading[] }> {
  return request<{ readings: IotReading[] }>(
    `/api/iot/readings?device_id=${encodeURIComponent(deviceId)}&limit=${limit}`,
  );
}

export function fetchDeviceAssessment(
  deviceId: string,
  lang: string,
): Promise<DeviceAssessment> {
  return request<DeviceAssessment>(
    `/api/iot/devices/${encodeURIComponent(deviceId)}/assessment?lang=${lang}`,
  );
}

export function fetchSimulation(): Promise<{ simulation: SimulationState }> {
  return request<{ simulation: SimulationState }>("/api/iot/simulation");
}

export function controlSimulation(
  action: "start" | "stop",
  options: { scenario?: SimulationScenario; device_id?: string; interval_seconds?: number } = {},
): Promise<{ simulation: SimulationState }> {
  return request<{ simulation: SimulationState }>("/api/iot/simulation", {
    method: "POST",
    body: JSON.stringify({ action, ...options }),
  });
}

export function saveReadingReport(
  readingId: number,
  lang: string,
  crop: string | null = null,
): Promise<{ id: number; saved: boolean; farmer_id: number | null }> {
  return request<{ id: number; saved: boolean; farmer_id: number | null }>(
    `/api/iot/readings/${readingId}/report`,
    { method: "POST", body: JSON.stringify({ lang, crop }) },
  );
}

/* ---------------- IoT → Farmer: alerts, Run Demo, export (Phase 3) ---------------- */

export type AlertSeverity = "info" | "warning" | "critical";
export type AlertStatus = "active" | "resolved";

export interface IotAlert {
  id: number;
  device: number;
  device_public_id: string;
  device_name: string;
  plot_id: number | null;
  farmer_id: number | null;
  alert_type: string;
  severity: AlertSeverity;
  status: AlertStatus;
  value: number | null;
  threshold: number | null;
  reading_id: number | null;
  message: string;
  created_at: string;
  resolved_at: string | null;
  resolved_reason: "acked" | "auto" | null;
}

export interface AlertsResponse {
  alerts: IotAlert[];
  active_count: number;
}

export interface DemoRunResponse {
  device_id: string;
  scenario: SimulationScenario;
  readings_created: number;
  active_alerts: number;
  device: IotDevice;
}

export function fetchAlerts(
  options: { deviceId?: string; status?: "active" | "resolved" | "all"; limit?: number } = {},
): Promise<AlertsResponse> {
  const params = new URLSearchParams();
  if (options.deviceId) params.set("device_id", options.deviceId);
  if (options.status) params.set("status", options.status);
  if (options.limit) params.set("limit", String(options.limit));
  const qs = params.toString();
  return request<AlertsResponse>(`/api/iot/alerts${qs ? `?${qs}` : ""}`);
}

export function ackAlert(
  alertId: number,
): Promise<{ alert: IotAlert; acknowledged: boolean; active_count: number }> {
  return request<{ alert: IotAlert; acknowledged: boolean; active_count: number }>(
    `/api/iot/alerts/${alertId}/ack`,
    { method: "POST" },
  );
}

export function runDemo(
  options: { scenario?: SimulationScenario; count?: number; reset?: boolean } = {},
): Promise<DemoRunResponse> {
  return request<DemoRunResponse>("/api/iot/demo/run", {
    method: "POST",
    body: JSON.stringify(options),
  });
}

/** Direct download URL for the CSV export endpoint (Content-Disposition attachment). */
export function readingsExportUrl(deviceId: string, limit: number = 500): string {
  return `${API_BASE}/api/iot/readings/export?device_id=${encodeURIComponent(deviceId)}&limit=${limit}`;
}
