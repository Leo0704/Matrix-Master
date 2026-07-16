/**
 * util/encrypt — chat history localStorage 加密（v0.7+ 业务模型重构）
 *
 * 算法：AES-GCM 256-bit（Web Crypto API），per-businessId 派生 key。
 *
 * localStorage 是明文，任何能读浏览器存储的人都能看到 chat 历史。
 * 加密主要防：
 * 1) 共享电脑 / 临时账号泄漏
 * 2) 浏览器扩展 / 调试脚本读取
 *
 * 不能防：恶意代码在同源上下文执行（因为 key 也在前端）。
 *
 * 设计取舍：
 * - 不持久化 key，每次启动从固定 passphrase + businessId 派生
 * - passphrase 写死（占位；生产应换成用户密码或 device keychain 派生）
 * - IV 每次加密随机生成，附在密文前面
 */

const PASSPHRASE = 'matrix-master-shell-encryption-v0.7';
const ALGO = { name: 'AES-GCM', length: 256 } as const;
const PBKDF2_PARAMS = { name: 'PBKDF2', hash: 'SHA-256', iterations: 100_000, salt: new TextEncoder().encode('matrix-master-shell') };

/**
 * 从 passphrase + businessId 派生 AES key（per-business 隔离）。
 * businessId 提供额外盐：同 passphrase 不同 business 的 key 不同。
 */
async function deriveKey(businessId: string | null): Promise<CryptoKey> {
  const enc = new TextEncoder();
  const baseKey = await crypto.subtle.importKey(
    'raw',
    enc.encode(PASSPHRASE),
    'PBKDF2',
    false,
    ['deriveKey'],
  );
  return crypto.subtle.deriveKey(
    { ...PBKDF2_PARAMS, salt: enc.encode(`matrix-master-shell:${businessId ?? 'unknown'}`) },
    baseKey,
    ALGO,
    false,
    ['encrypt', 'decrypt'],
  );
}

/**
 * 加密字符串 → base64（含 12 字节 IV）。
 * 返回格式：base64(IV[12] + ciphertext+N)。
 */
export async function encryptString(plaintext: string, businessId: string | null): Promise<string> {
  if (!crypto?.subtle) {
    throw new Error('Web Crypto API not available');
  }
  const key = await deriveKey(businessId);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const enc = new TextEncoder();
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, enc.encode(plaintext)),
  );
  // 拼接 IV + ciphertext
  const out = new Uint8Array(iv.length + ciphertext.length);
  out.set(iv, 0);
  out.set(ciphertext, iv.length);
  // base64 编码
  return base64Encode(out);
}

/**
 * 解密（encryptString 的逆操作）。失败抛 Error（含原因）。
 */
export async function decryptString(payload: string, businessId: string | null): Promise<string> {
  if (!crypto?.subtle) {
    throw new Error('Web Crypto API not available');
  }
  const data = base64Decode(payload);
  if (data.length < 13) {
    throw new Error('ciphertext too short');
  }
  const iv = data.slice(0, 12);
  const ciphertext = data.slice(12);
  const key = await deriveKey(businessId);
  const dec = new TextDecoder();
  return dec.decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ciphertext));
}

// ---------------------------------------------------------------------------
// base64 helpers（atob/btoa 不支持 Uint8Array，自己写）
// ---------------------------------------------------------------------------

function base64Encode(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]!);
  return btoa(bin);
}

function base64Decode(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}