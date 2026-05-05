import Head from 'next/head';
import Link from 'next/link';
import ReviewerAuthControl from '../components/ReviewerAuthControl';
import { useReviewerAuth } from '../hooks/useReviewerAuth';

const cards = [
  { href: '/conflicts', title: 'Conflict queue', desc: 'Resolve field-level source disagreements.' },
  { href: '/duplicates', title: 'Duplicate candidates', desc: 'Review cross-source event matches before merging.' },
  { href: '/data-quality', title: 'Data-quality issues', desc: 'Resolve split-vs-total inconsistencies and source warnings.' },
  { href: '/admin', title: 'Admin console', desc: 'Audit log, source freshness, archive manifests, API keys, and source docs.' },
];

export default function OperatorHome() {
  const auth = useReviewerAuth();
  return (
    <>
      <Head><title>Operator Console — Aviation Safety Atlas</title></Head>
      <main className="min-h-screen bg-stone-50">
        <header className="border-b border-stone-200 bg-white px-6 py-4 flex items-center gap-4">
          <Link href="/" className="text-[11px] text-stone-400 hover:text-stone-700">← search</Link>
          <div className="flex-1">
            <h1 className="text-[18px] font-semibold text-stone-800">Operator Console</h1>
            <p className="text-[11px] text-stone-400">Reviewer/admin workflows that should not be hidden behind raw API calls.</p>
          </div>
          <ReviewerAuthControl apiKey={auth.apiKey} onApiKeyChange={auth.setApiKey} compact />
        </header>
        <section className="max-w-5xl mx-auto p-6 grid md:grid-cols-2 gap-4">
          {cards.map((card) => (
            <Link key={card.href} href={card.href} className="bg-white border border-stone-200 rounded-xl p-5 hover:border-stone-400 transition-colors">
              <div className="text-[14px] font-semibold text-stone-800">{card.title}</div>
              <p className="text-[12px] text-stone-500 mt-1">{card.desc}</p>
              <div className="text-[10px] text-stone-400 mt-4" style={{ fontFamily: 'var(--ff-mono)' }}>open →</div>
            </Link>
          ))}
        </section>
      </main>
    </>
  );
}
