import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { BellPlus, X } from 'lucide-react';
import { alertsApi } from '../../api/alerts';
import { getParsedApiError } from '../../api/error';
import { stocksApi } from '../../api/stocks';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { AlertRuleItem, AlertType, TrailingStopMode } from '../../types/alerts';
import type { PortfolioPositionItem } from '../../types/portfolio';
import { Button, InlineAlert, Input, Select } from '../common';

type PositionWithAccount = PortfolioPositionItem & {
  accountId: number;
  accountName: string;
};

interface PositionAlertDialogProps {
  position: PositionWithAccount | null;
  onClose: () => void;
  onCreated: (rule: AlertRuleItem) => void;
}

function toInputValue(value: number): string {
  return Number.isFinite(value) && value > 0 ? String(Number(value.toFixed(4))) : '';
}

export const PositionAlertDialog: React.FC<PositionAlertDialogProps> = ({
  position,
  onClose,
  onCreated,
}) => {
  const { language } = useUiLanguage();
  const [alertType, setAlertType] = useState<Extract<AlertType, 'price_cross' | 'trailing_stop'>>('price_cross');
  const [price, setPrice] = useState('');
  const [activationPrice, setActivationPrice] = useState('');
  const [trailMode, setTrailMode] = useState<TrailingStopMode>('percent');
  const [trailValue, setTrailValue] = useState('5');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isQuoteLoading, setIsQuoteLoading] = useState(false);
  const [currentPrice, setCurrentPrice] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const isProfitable = Boolean(
    position?.priceAvailable !== false
    && (position?.lastPrice ?? 0) > 0
    && (position?.unrealizedPnlBase ?? 0) > 0,
  );

  useEffect(() => {
    if (!position) return;
    let cancelled = false;
    const fallbackPrice = position.lastPrice || position.avgCost;
    const nextType = isProfitable ? 'trailing_stop' : 'price_cross';
    setAlertType(nextType);
    setPrice(toInputValue(position.avgCost));
    setCurrentPrice(position.lastPrice || 0);
    setActivationPrice(toInputValue(fallbackPrice));
    setTrailMode('percent');
    setTrailValue('5');
    setError(null);
    setIsSubmitting(false);
    setIsQuoteLoading(true);
    void stocksApi.getQuote(position.symbol)
      .then((quote) => {
        if (cancelled || !Number.isFinite(quote.currentPrice) || quote.currentPrice <= 0) return;
        setCurrentPrice(quote.currentPrice);
        setActivationPrice(toInputValue(quote.currentPrice));
        setAlertType(quote.currentPrice > position.avgCost ? 'trailing_stop' : 'price_cross');
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setIsQuoteLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isProfitable, position]);

  const amountDefault = useMemo(() => {
    const reference = Number(activationPrice) || currentPrice || position?.avgCost || 0;
    return Math.max(0.01, Number((reference * 0.05).toFixed(2)));
  }, [activationPrice, currentPrice, position]);

  if (!position) return null;

  const parsePositive = (raw: string, label: string): number | null => {
    const value = Number(raw);
    if (!Number.isFinite(value) || value <= 0) {
      setError(language === 'zh' ? `${label}必须大于 0` : `${label} must be greater than 0`);
      return null;
    }
    return value;
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    let parameters;
    if (alertType === 'price_cross') {
      const threshold = parsePositive(price, language === 'zh' ? '目标价格' : 'Target price');
      if (threshold == null) return;
      parameters = { direction: 'above' as const, price: threshold };
    } else {
      const activation = parsePositive(activationPrice, language === 'zh' ? '启用价格' : 'Activation price');
      const drawdown = parsePositive(trailValue, language === 'zh' ? '回撤值' : 'Drawdown');
      if (activation == null || drawdown == null) return;
      if (trailMode === 'percent' && drawdown >= 100) {
        setError(language === 'zh' ? '回撤比例必须小于 100%' : 'Drawdown must be less than 100%');
        return;
      }
      parameters = { activationPrice: activation, trailMode, trailValue: drawdown };
    }

    setIsSubmitting(true);
    try {
      const rule = await alertsApi.createRule({
        name: alertType === 'trailing_stop'
          ? `${position.symbol} 跟踪止损`
          : `${position.symbol} 回本价提醒`,
        targetScope: 'single_symbol',
        target: position.symbol,
        alertType,
        parameters,
        severity: 'warning',
        enabled: true,
      });
      onCreated(rule);
    } catch (err) {
      setError(getParsedApiError(err).message || (language === 'zh' ? '创建告警失败' : 'Failed to create alert'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 px-4 py-6" role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="position-alert-dialog-title"
        className="w-full max-w-lg rounded-lg border border-border bg-elevated p-5 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <BellPlus className="h-5 w-5 text-cyan" />
              <h2 id="position-alert-dialog-title" className="text-base font-semibold text-foreground">
                {language === 'zh' ? `为 ${position.symbol} 创建告警` : `Create alert for ${position.symbol}`}
              </h2>
            </div>
            <p className="mt-1 text-xs text-secondary-text">
              {position.accountName} · {language === 'zh' ? '成本' : 'Cost'} {position.avgCost.toFixed(4)} · {language === 'zh' ? '现价' : 'Last'} {currentPrice > 0 ? currentPrice.toFixed(4) : '--'}{isQuoteLoading ? (language === 'zh' ? '（更新中）' : ' (updating)') : ''}
            </p>
          </div>
          <button
            type="button"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-secondary-text hover:bg-hover hover:text-foreground"
            onClick={onClose}
            aria-label={language === 'zh' ? '关闭' : 'Close'}
            title={language === 'zh' ? '关闭' : 'Close'}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form className="mt-5 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
          <Select
            label={language === 'zh' ? '规则类型' : 'Rule type'}
            value={alertType}
            options={[
              { value: 'price_cross', label: language === 'zh' ? '价格超过目标价' : 'Price above target' },
              { value: 'trailing_stop', label: language === 'zh' ? '跟踪止损' : 'Trailing stop' },
            ]}
            disabled={isSubmitting}
            onChange={(value) => setAlertType(value as 'price_cross' | 'trailing_stop')}
          />

          {alertType === 'price_cross' ? (
            <Input
              label={language === 'zh' ? '目标价格' : 'Target price'}
              type="number"
              min="0"
              step="0.0001"
              value={price}
              onChange={(event) => setPrice(event.target.value)}
              disabled={isSubmitting}
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Input
                label={language === 'zh' ? '高于此价格后开始跟踪' : 'Start tracking above'}
                type="number"
                min="0"
                step="0.0001"
                value={activationPrice}
                onChange={(event) => setActivationPrice(event.target.value)}
                disabled={isSubmitting}
              />
              <Select
                label={language === 'zh' ? '回撤方式' : 'Drawdown mode'}
                value={trailMode}
                options={[
                  { value: 'percent', label: language === 'zh' ? '按比例' : 'Percent' },
                  { value: 'amount', label: language === 'zh' ? '按金额' : 'Amount' },
                ]}
                disabled={isSubmitting}
                onChange={(value) => {
                  const nextMode = value as TrailingStopMode;
                  setTrailMode(nextMode);
                  setTrailValue(nextMode === 'percent' ? '5' : String(amountDefault));
                }}
              />
              <Input
                className="sm:col-span-2"
                label={trailMode === 'percent'
                  ? (language === 'zh' ? '从最高价回撤比例（%）' : 'Drawdown from peak (%)')
                  : (language === 'zh' ? '从最高价回撤金额' : 'Drawdown amount from peak')}
                type="number"
                min="0"
                step="0.01"
                value={trailValue}
                onChange={(event) => setTrailValue(event.target.value)}
                disabled={isSubmitting}
              />
            </div>
          )}

          {error ? <InlineAlert variant="danger" message={error} className="rounded-lg px-3 py-2 text-xs shadow-none" /> : null}

          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" onClick={onClose} disabled={isSubmitting}>
              {language === 'zh' ? '取消' : 'Cancel'}
            </Button>
            <Button type="submit" isLoading={isSubmitting} loadingText={language === 'zh' ? '创建中...' : 'Creating...'}>
              <BellPlus className="h-4 w-4" />
              {language === 'zh' ? '创建并启用' : 'Create and enable'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
};
