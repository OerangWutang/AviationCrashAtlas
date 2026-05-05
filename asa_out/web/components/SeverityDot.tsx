import { SEV_COLOR } from '../lib/utils';

interface Props {
  severity: string | null;
  size?: number;
}

export default function SeverityDot({ severity, size = 7 }: Props) {
  const color = SEV_COLOR[severity ?? 'UNKNOWN'] ?? SEV_COLOR.UNKNOWN;
  return (
    <span
      className="inline-block rounded-full flex-shrink-0"
      style={{ width: size, height: size, background: color }}
    />
  );
}
