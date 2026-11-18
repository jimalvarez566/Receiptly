// Backend datetimes are naive UTC strings without a zone designator.
const HAS_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i

export function parseUtc(iso: string): Date {
  return new Date(HAS_ZONE.test(iso) ? iso : `${iso}Z`)
}
