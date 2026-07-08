import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import type { KbDocument, KbDocumentCreate } from '@/types/api';

interface ProductFormProps {
  initial?: KbDocument;
  onSubmit: (body: KbDocumentCreate) => Promise<void>;
  onCancel?: () => void;
  submitting?: boolean;
}

/**
 * 商品库表单：必填 content (一段自由文本描述)，可选 metadata {price, sizes, style, category, product_sku}。
 */
export function ProductForm({ initial, onSubmit, onCancel, submitting }: ProductFormProps) {
  const [title, setTitle] = useState(initial?.title ?? '');
  const [content, setContent] = useState(initial?.content ?? '');
  const [price, setPrice] = useState<string>(
    (initial?.metadata?.price as string | number | undefined)?.toString() ?? '',
  );
  const [sizes, setSizes] = useState<string>(
    Array.isArray(initial?.metadata?.sizes)
      ? (initial?.metadata?.sizes as unknown[]).join(', ')
      : '',
  );
  const [style, setStyle] = useState<string>(
    (initial?.metadata?.style as string | undefined) ?? '',
  );
  const [category, setCategory] = useState<string>(
    (initial?.metadata?.category as string | undefined) ?? '',
  );
  const [sku, setSku] = useState<string>(
    (initial?.metadata?.product_sku as string | undefined) ?? '',
  );

  useEffect(() => {
    if (initial) {
      setTitle(initial.title ?? '');
      setContent(initial.content);
      setPrice((initial.metadata?.price as string | number | undefined)?.toString() ?? '');
      setSizes(
        Array.isArray(initial.metadata?.sizes)
          ? (initial.metadata?.sizes as unknown[]).join(', ')
          : '',
      );
      setStyle((initial.metadata?.style as string | undefined) ?? '');
      setCategory((initial.metadata?.category as string | undefined) ?? '');
      setSku((initial.metadata?.product_sku as string | undefined) ?? '');
    }
  }, [initial]);

  async function handleSubmit() {
    if (!content.trim()) return;
    const metadata: Record<string, unknown> = {};
    if (price.trim()) metadata.price = price.trim();
    if (sizes.trim()) {
      metadata.sizes = sizes
        .split(/[,，\s]+/)
        .map((s) => s.trim())
        .filter(Boolean);
    }
    if (style.trim()) metadata.style = style.trim();
    if (category.trim()) metadata.category = category.trim();
    if (sku.trim()) metadata.product_sku = sku.trim();
    await onSubmit({
      type: 'product',
      title: title.trim() || undefined,
      content: content.trim(),
      metadata,
      is_published: false,
    });
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label htmlFor="product-title">商品标题（可选）</Label>
        <Input
          id="product-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="例：小白鞋 2024 春夏新款"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="product-content">商品描述（必填）</Label>
        <Textarea
          id="product-content"
          rows={4}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="例：这款小白鞋采用透气帆布面，橡胶防滑底，百搭通勤…"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="product-price">价格</Label>
          <Input
            id="product-price"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="例：199 元"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="product-sku">SKU</Label>
          <Input
            id="product-sku"
            value={sku}
            onChange={(e) => setSku(e.target.value)}
            placeholder="例：SKU-2024-001"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="product-style">风格</Label>
          <Input
            id="product-style"
            value={style}
            onChange={(e) => setStyle(e.target.value)}
            placeholder="例：平价 / 极简 / 通勤"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="product-category">类目</Label>
          <Input
            id="product-category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="例：鞋子 / 上衣 / 美妆"
          />
        </div>
      </div>
      <div className="space-y-1">
        <Label htmlFor="product-sizes">尺码（逗号分隔）</Label>
        <Input
          id="product-sizes"
          value={sizes}
          onChange={(e) => setSizes(e.target.value)}
          placeholder="例：35, 36, 37, 38, 39, 40"
        />
      </div>
      <div className="flex items-center justify-end gap-2 pt-2">
        {onCancel && (
          <Button variant="ghost" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
        )}
        <Button onClick={handleSubmit} disabled={submitting || !content.trim()}>
          {submitting ? '保存中…' : initial ? '更新' : '创建'}
        </Button>
      </div>
    </div>
  );
}
