const TIME_ZONE = 'Europe/Zagreb';

const isoDay = new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' });
const longDay = new Intl.DateTimeFormat('en-GB', { timeZone: TIME_ZONE, year: 'numeric', month: 'long', day: 'numeric' });
const monthYear = new Intl.DateTimeFormat('en-GB', { timeZone: TIME_ZONE, year: 'numeric', month: 'short' });

/** 2026-10-21 (the format the old site used for news). */
export const formatIsoDay = (date: Date) => isoDay.format(date);

/** 21 October 2026 */
export const formatLongDay = (date: Date) => longDay.format(date);

/** Oct 2026 */
export const formatMonthYear = (date: Date) => monthYear.format(date);

export const yearOf = (date: Date) => Number(isoDay.format(date).slice(0, 4));
