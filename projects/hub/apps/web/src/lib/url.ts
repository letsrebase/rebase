/** Whether an address can open in a new tab as a web page (`http:` or `https:`). Every
 *  `href` that comes from data (a LinkedIn profile, a CRM deal, a signing page) passes
 *  through this before it reaches an anchor: the server normalises these values, but a
 *  row written before that rule can hold any text, and a `javascript:` address must
 *  never become a link. */
export function isHttpUrl(value: string): boolean {
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol)
  } catch {
    return false
  }
}
