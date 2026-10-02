/** Home research summary cards — never treat missing metrics as zero dollars. */

export function shadowCapitalSummaryMode(
  competitionUnavailable: boolean,
  shadowReferenceCapital: number | null | undefined
): "unavailable" | "ready" {
  if (competitionUnavailable || shadowReferenceCapital == null) {
    return "unavailable";
  }
  return "ready";
}

export function brokerEquitySummaryMode(
  loading: boolean,
  equity: number | null | undefined
): "loading" | "unavailable" | "ready" {
  if (loading && equity == null) {
    return "loading";
  }
  if (equity == null) {
    return "unavailable";
  }
  return "ready";
}
