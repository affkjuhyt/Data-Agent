import type { ChatCommandName } from './chatCommands';

export type AnswerLayout = 'auto' | 'chart_first' | 'sql_first' | 'data_first' | 'report';
export type ResponseSection =
  | 'progress'
  | 'reading'
  | 'content'
  | 'partial'
  | 'clarification'
  | 'chart'
  | 'drill'
  | 'table'
  | 'proposal'
  | 'evidence'
  | 'verification'
  | 'citations'
  | 'meta'
  | 'suggestions';

export function layoutForCommand(command: ChatCommandName): AnswerLayout {
  switch (command) {
    case 'chart':
      return 'chart_first';
    case 'sql':
      return 'sql_first';
    case 'data':
      return 'data_first';
    case 'report':
      return 'report';
    default:
      return 'auto';
  }
}

export function responseOrder(layout: AnswerLayout, section: ResponseSection): number {
  const orders: Record<AnswerLayout, ResponseSection[]> = {
    auto: ['progress', 'reading', 'content', 'partial', 'clarification', 'chart', 'drill', 'table', 'proposal', 'evidence', 'verification', 'citations', 'meta', 'suggestions'],
    chart_first: ['progress', 'chart', 'content', 'reading', 'table', 'drill', 'evidence', 'proposal', 'verification', 'citations', 'meta', 'suggestions', 'partial', 'clarification'],
    sql_first: ['progress', 'evidence', 'table', 'content', 'reading', 'chart', 'drill', 'proposal', 'verification', 'citations', 'meta', 'suggestions', 'partial', 'clarification'],
    data_first: ['progress', 'table', 'content', 'reading', 'chart', 'drill', 'evidence', 'proposal', 'verification', 'citations', 'meta', 'suggestions', 'partial', 'clarification'],
    report: ['progress', 'content', 'reading', 'chart', 'table', 'evidence', 'proposal', 'verification', 'citations', 'meta', 'suggestions', 'partial', 'clarification', 'drill'],
  };
  return orders[layout].indexOf(section);
}
