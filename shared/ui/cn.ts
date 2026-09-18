import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * The class merger every primitive in this package composes with, and the one the two
 * applications import for their own components too. It was the same five lines in
 * `projects/pigrocrm/apps/web/src/lib/utils.ts` and in the hub's copy until REB-300;
 * both are deleted.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
