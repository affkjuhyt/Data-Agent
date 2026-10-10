import { useCallback, useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AskBar } from './AskBar';
import { NeedsYouCard } from './NeedsYouCard';
import { fetchPendingReviews, recentQuestions, type PendingReview } from './api';

/*
 * "v3.1 · Trang chủ" (2132:15338, UC-01-01, UC-08-05): the page everyone lands on.
 * Two columns under 16×32 padding with a 24px gap: the main column (greeting, question bar)
 * and a 380px right column (what needs you). Blocks without a data source yet are left out
 * rather than drawn with made-up numbers.
 */
const WEEKDAYS = ['Chủ Nhật', 'Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy'];

function greetingFor(hour: number): string {
  if (hour < 11) return 'Chào buổi sáng';
  if (hour < 13) return 'Chào buổi trưa';
  if (hour < 18) return 'Chào buổi chiều';
  return 'Chào buổi tối';
}

/** Vietnamese names put the family name first: "Nguyễn Hải Yến" is greeted as "Hải Yến". */
function givenName(fullName: string): string {
  const words = fullName.trim().split(/\s+/);
  return words.length > 1 ? words.slice(1).join(' ') : fullName;
}

export default function HomePage() {
  const { user, tenant, navigate } = useAppState();
  const [reviews, setReviews] = useState<PendingReview[] | undefined>(undefined);
  const [reviewsError, setReviewsError] = useState(false);

  const loadReviews = useCallback(() => {
    setReviewsError(false);
    fetchPendingReviews()
      .then((list) => setReviews(Array.isArray(list) ? list : []))
      .catch(() => setReviewsError(true));
  }, []);
  useEffect(loadReviews, [loadReviews]);

  const now = new Date();
  const dateLine = `${WEEKDAYS[now.getDay()]}, ${String(now.getDate()).padStart(2, '0')}/${String(now.getMonth() + 1).padStart(2, '0')}`;

  const ask = (question: string) => {
    window.history.replaceState(null, '', `/analysis/ask?q=${encodeURIComponent(question)}`);
    navigate('analysis', 'ASK');
  };

  return (
    <div className="flex items-start gap-da-2xl px-da-3xl py-da-xl max-[1279px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col gap-[20px] max-[1279px]:w-full">
        <header className="flex flex-col gap-da-xs">
          <p className="uppercase text-muted-foreground da-overline">
            {dateLine} · {tenant.tenantName ?? 'Toàn tổ chức'}
          </p>
          <h1 className="text-foreground da-heading-1">
            {greetingFor(now.getHours())}, {givenName(user.name)}
          </h1>
        </header>
        <AskBar suggestions={recentQuestions(4)} onAsk={ask} />
      </div>

      <aside className="flex w-[380px] shrink-0 flex-col gap-da-xl max-[1279px]:w-full">
        <NeedsYouCard
          reviews={reviews}
          error={reviewsError}
          onOpen={() => navigate('needs-you')}
          onRetry={loadReviews}
        />
      </aside>
    </div>
  );
}
