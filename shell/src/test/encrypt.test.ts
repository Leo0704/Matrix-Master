/**
 * util/encrypt 单元测试（v0.7+ chat history 加密）。
 *
 * 验证：
 * - 加密往返：明文 → 密文 → 明文（一致）
 * - 同明文 + 不同 businessId → 不同密文（per-business 隔离）
 * - 篡改密文 → 解密失败（抛 Error）
 * - 加密输出包含 base64 字符
 */
import { describe, expect, it } from 'vitest';
import { encryptString, decryptString } from '@/lib/encrypt';

describe('util/encrypt', () => {
  it('加密往返：明文 → 密文 → 明文 一致', async () => {
    const plaintext = '你好，世界！This is a chat message with emoji 🚀';
    const biz = 'biz-test-001';
    const encrypted = await encryptString(plaintext, biz);
    expect(encrypted).not.toBe(plaintext); // 密文 ≠ 明文
    const decrypted = await decryptString(encrypted, biz);
    expect(decrypted).toBe(plaintext);
  });

  it('per-business 隔离：同明文不同 businessId → 不同密文', async () => {
    const plaintext = '一样的明文';
    const encA = await encryptString(plaintext, 'biz-a');
    const encB = await encryptString(plaintext, 'biz-b');
    expect(encA).not.toBe(encB);

    // 跨业务解密失败（key 不同）
    await expect(decryptString(encA, 'biz-b')).rejects.toThrow();
    await expect(decryptString(encB, 'biz-a')).rejects.toThrow();
  });

  it('业务 ID 为 null 时也能加密解密', async () => {
    const plaintext = 'unknown business';
    const enc = await encryptString(plaintext, null);
    const dec = await decryptString(enc, null);
    expect(dec).toBe(plaintext);
  });

  it('篡改密文 → 解密抛 Error', async () => {
    const enc = await encryptString('hello', 'biz-1');
    // 翻转一个字符（篡改）
    const tampered = enc.slice(0, -2) + (enc.endsWith('A') ? 'B' : 'A') + enc.slice(-1);
    await expect(decryptString(tampered, 'biz-1')).rejects.toThrow();
  });

  it('密文格式：base64 字符（IV + ciphertext）', async () => {
    const enc = await encryptString('test', 'biz');
    // base64 字符集：A-Z a-z 0-9 + / =
    expect(enc).toMatch(/^[A-Za-z0-9+/=]+$/);
    // IV 12 字节 + 4 字节 ciphertext = 16 字节 → base64 24 字符（实际会更多因为有 padding）
    expect(enc.length).toBeGreaterThanOrEqual(20);
  });
});