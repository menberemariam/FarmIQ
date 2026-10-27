/** Unit conversion between EthioSIS/ESRI lab-report units and the model's
 * kg/ha scale. Factors mirror ethiopian_standards.py defaults on the backend
 * (N factor 400, K cmolc factor 391); P is 1:1 at plow-layer scale.
 *
 * The UI keeps all state in kg/ha — lab mode is a display overlay that
 * converts into the same kg/ha state, so saved reports, history deltas and
 * fertilizer math always stay in model units.
 */

export const N_PCT_TO_KG_HA = 400;
export const K_CMOLC_TO_KG_HA = 391;

export const pctNToKgHa = (v: number): number => v * N_PCT_TO_KG_HA;
export const kgHaToPctN = (v: number): number => v / N_PCT_TO_KG_HA;
export const cmolcKToKgHa = (v: number): number => v * K_CMOLC_TO_KG_HA;
export const kgHaToCmolcK = (v: number): number => v / K_CMOLC_TO_KG_HA;
