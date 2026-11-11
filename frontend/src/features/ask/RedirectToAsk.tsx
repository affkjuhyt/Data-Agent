import { useEffect } from 'react';
import { useAppState } from '../../AppStateContext';

/** Legacy `/analysis/doc_ask` lands here so old bookmarks do not 404; Ask is the only ask surface. */
export default function RedirectToAsk() {
  const { navigate } = useAppState();
  useEffect(() => {
    navigate('analysis', 'ASK');
  }, [navigate]);
  return null;
}
