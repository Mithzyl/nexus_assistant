/** Select one valid service date from a notification link, or use the plan's normal default. */
export function linkedCommuteDate(values, defaultDate) {
  const value = values.length === 1 ? values[0] : "";
  if (/^(?!0000)\d{4}-\d{2}-\d{2}$/.test(value)) {
    const date = new Date(value + "T00:00:00Z");
    if (Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value) return value;
  }
  return defaultDate();
}
