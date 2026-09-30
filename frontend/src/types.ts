export type ExtractMethod = "text" | "ocr" | "hybrid";

export interface PageResult {
  index: number;
  text: string;
  method: ExtractMethod;
}

export interface ParseResult {
  filename: string;
  extension: string;
  media_type: string;
  method: ExtractMethod;
  page_count: number;
  char_count: number;
  text: string;
  pages: PageResult[];
}

export interface SupportedTypes {
  extensions: string[];
  ocr_available: boolean;
  ocr_languages: string[];
}

export type DatabaseType = "sqlite" | "mysql" | "postgresql" | "sqlserver";

export interface DatabaseTypeOption {
  value: DatabaseType;
  label: string;
  default_port: number | null;
  default_driver: string | null;
}

export interface DatabaseConfigPublic {
  configured: boolean;
  type: DatabaseType;
  host: string;
  port: number | null;
  database: string;
  username: string;
  password_set: boolean;
  charset: string;
  driver: string | null;
  config_file: string;
}

export interface DatabaseConfigPayload {
  type: DatabaseType;
  host: string;
  port: number | null;
  database: string;
  username: string;
  password: string | null;
  charset: string;
  driver: string | null;
}

export interface DatabaseTestResult {
  success: boolean;
  message: string;
  dialect: string | null;
  server_version: string | null;
  elapsed_ms: number | null;
}

export interface DatabaseStatus {
  configured: boolean;
  reachable: boolean;
  message: string;
  dialect: string | null;
  server_version: string | null;
  config_file: string;
}

export interface Category {
  id: number;
  code: string;
  name: string;
  sort_order: number;
  is_enabled: boolean;
  remark: string | null;
  created_by: string | null;
  created_datetime: string | null;
  updated_by: string | null;
  updated_datetime: string | null;
}

/** 后端统一分页返回结构 */
export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface CategoryCreatePayload {
  name: string;
  sort_order: number;
  is_enabled: boolean;
  remark: string | null;
}

export interface CategoryUpdatePayload {
  name?: string;
  sort_order?: number;
  is_enabled?: boolean;
  remark?: string | null;
}

export interface Template {
  id: number;
  code: string;
  category_id: number;
  category_name: string | null;
  name: string;
  dpi: number | null;
  width: number | null;
  height: number | null;
  ref_image_b64?: string | null;
  /** 模具保存后返回：共享目录备份相对路径 */
  backup_path?: string | null;
  backup_ok?: boolean | null;
  backup_error?: string | null;
  is_enabled: boolean;
  created_by: string | null;
  created_datetime: string | null;
  updated_by: string | null;
  updated_datetime: string | null;
}

export interface TemplateCreatePayload {
  category_id: number;
  name: string;
  is_enabled: boolean;
  dpi?: number | null;
  width?: number | null;
  height?: number | null;
  ref_image?: string | null;
}

export interface TemplateUpdatePayload {
  category_id?: number;
  name?: string;
  is_enabled?: boolean;
  dpi?: number | null;
  width?: number | null;
  height?: number | null;
  ref_image?: string | null;
}

export interface PathConfig {
  id: number;
  code: string;
  name: string;
  path: string;
  is_enabled: boolean;
  remark: string | null;
  created_by: string | null;
  created_datetime: string | null;
  updated_by: string | null;
  updated_datetime: string | null;
}

export interface PathConfigCreatePayload {
  code: string;
  name: string;
  path: string;
  is_enabled: boolean;
  remark: string | null;
}

export interface PathConfigUpdatePayload {
  name?: string;
  path?: string;
  is_enabled?: boolean;
  remark?: string | null;
}
