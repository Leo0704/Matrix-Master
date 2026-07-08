export * from './api';

// Re-export common helpers
export type Optional<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;
export type WithRequired<T, K extends keyof T> = T & { [P in K]-?: T[P] };
