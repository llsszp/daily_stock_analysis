import type React from 'react';
import { useEffect, useState } from 'react';
import { Pencil, X } from 'lucide-react';
import { portfolioApi } from '../../api/portfolio';
import { getParsedApiError } from '../../api/error';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { PortfolioPositionItem } from '../../types/portfolio';
import { Button, InlineAlert, Input } from '../common';

type EditablePosition = PortfolioPositionItem & {
  accountId: number;
  accountName: string;
};

interface PositionEditDialogProps {
  position: EditablePosition | null;
  onClose: () => void;
  onSaved: (symbol: string) => void;
}

export const PositionEditDialog: React.FC<PositionEditDialogProps> = ({
  position,
  onClose,
  onSaved,
}) => {
  const { language } = useUiLanguage();
  const [quantity, setQuantity] = useState('');
  const [avgCost, setAvgCost] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!position) return;
    setQuantity(String(position.quantity));
    setAvgCost(String(position.avgCost));
    setError(null);
    setIsSubmitting(false);
  }, [position]);

  if (!position) return null;

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextQuantity = Number(quantity);
    const nextAvgCost = Number(avgCost);
    if (!Number.isFinite(nextQuantity) || nextQuantity <= 0 || !Number.isFinite(nextAvgCost) || nextAvgCost <= 0) {
      setError(language === 'zh' ? '持仓数量和平均成本必须大于 0' : 'Quantity and average cost must be greater than 0');
      return;
    }
    setError(null);
    setIsSubmitting(true);
    try {
      await portfolioApi.updatePosition(position.symbol, {
        accountId: position.accountId,
        quantity: nextQuantity,
        avgCost: nextAvgCost,
        market: position.market as 'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'tw',
        currency: position.currency,
      });
      onSaved(position.symbol);
    } catch (err) {
      setError(getParsedApiError(err).message || (language === 'zh' ? '修改持仓失败' : 'Failed to update position'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 px-4 py-6" role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="position-edit-dialog-title"
        className="w-full max-w-md rounded-lg border border-border bg-elevated p-5 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Pencil className="h-5 w-5 text-cyan" />
              <h2 id="position-edit-dialog-title" className="text-base font-semibold text-foreground">
                {language === 'zh' ? `编辑 ${position.symbol} 持仓` : `Edit ${position.symbol} position`}
              </h2>
            </div>
            <p className="mt-1 text-xs text-secondary-text">
              {position.accountName} · {language === 'zh' ? '保存后将合并为一条当前持仓记录' : 'Saving consolidates this symbol into one current holding record'}
            </p>
          </div>
          <button
            type="button"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-secondary-text hover:bg-hover hover:text-foreground"
            onClick={onClose}
            aria-label={language === 'zh' ? '关闭' : 'Close'}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form className="mt-5 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
          <Input
            label={language === 'zh' ? '持仓数量' : 'Quantity'}
            type="number"
            min="0"
            step="0.000001"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
            disabled={isSubmitting}
          />
          <Input
            label={language === 'zh' ? '平均成本' : 'Average cost'}
            type="number"
            min="0"
            step="0.0001"
            value={avgCost}
            onChange={(event) => setAvgCost(event.target.value)}
            disabled={isSubmitting}
          />
          {error ? <InlineAlert variant="danger" message={error} className="rounded-lg px-3 py-2 text-xs shadow-none" /> : null}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" onClick={onClose} disabled={isSubmitting}>
              {language === 'zh' ? '取消' : 'Cancel'}
            </Button>
            <Button type="submit" isLoading={isSubmitting} loadingText={language === 'zh' ? '保存中...' : 'Saving...'}>
              <Pencil className="h-4 w-4" />
              {language === 'zh' ? '保存修改' : 'Save changes'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
};
