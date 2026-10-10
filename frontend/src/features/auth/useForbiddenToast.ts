import { useEffect } from 'react';
import { useToast } from '../../design-system/Toast';

/* lib/v1/client.ts announces a 403 as a window event; show it as a toast wherever the user is. */
export function useForbiddenToast() {
  const toast = useToast();
  useEffect(() => {
    const onForbidden = (event: Event) =>
      toast({
        tone: 'error',
        message: (event as CustomEvent<string>).detail || 'Bạn không có quyền làm thao tác này.',
      });
    window.addEventListener('app:forbidden', onForbidden);
    return () => window.removeEventListener('app:forbidden', onForbidden);
  }, [toast]);
}
