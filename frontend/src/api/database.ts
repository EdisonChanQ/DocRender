import { jsonRequest, request } from "./client";
import type {
  DatabaseConfigPayload,
  DatabaseConfigPublic,
  DatabaseStatus,
  DatabaseTestResult,
  DatabaseTypeOption,
} from "../types";

export function fetchDatabaseTypes(): Promise<DatabaseTypeOption[]> {
  return request<DatabaseTypeOption[]>("/database/types");
}

export function fetchDatabaseConfig(): Promise<DatabaseConfigPublic> {
  return request<DatabaseConfigPublic>("/database/config");
}

export function saveDatabaseConfig(
  payload: DatabaseConfigPayload,
): Promise<DatabaseConfigPublic> {
  return jsonRequest<DatabaseConfigPublic>("/database/config", "PUT", payload);
}

export function testDatabaseConnection(
  payload: DatabaseConfigPayload | null,
): Promise<DatabaseTestResult> {
  return jsonRequest<DatabaseTestResult>("/database/test", "POST", payload ?? undefined);
}

export function fetchDatabaseStatus(): Promise<DatabaseStatus> {
  return request<DatabaseStatus>("/database/status");
}
