import { createContext, useContext } from "react";
import type { CareerClient } from "./api/client";
import type { CatalogResponse, UserIdentity } from "./api/types";

export interface AppContextValue {
  client: CareerClient;
  catalog: CatalogResponse | null;
  mode: "live" | "demo";
  user: UserIdentity;
  handleError: (error: unknown) => string;
}
export const AppContext = createContext<AppContextValue | null>(null);
export function useApp() {
  const value = useContext(AppContext);
  if (!value) throw new Error("App context is missing");
  return value;
}
