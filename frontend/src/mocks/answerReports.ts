import { http, HttpResponse } from 'msw';
import type { AnswerReport, NewAnswerReport } from '../lib/v1/answerReports';

/*
 * MSW handler for lib/v1/answerReports.ts (PROPOSAL endpoint, UC-01-05). Reports live in memory;
 * a report is accepted for review and opens a proposal, as "Báo sai" in Figma describes.
 */
const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    {
      type: `https://docs.dataagent/errors/${code}`,
      title: code,
      status,
      code,
      detail,
      trace_id: 'mock',
    },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const KINDS = ['misread_question', 'wrong_number', 'wrong_source', 'incomplete'];

export function createAnswerReportHandlers() {
  const reports: AnswerReport[] = [];

  return [
    http.post('*/v1/answer-reports', async ({ request }) => {
      const body = (await request.json()) as NewAnswerReport;
      if (!body?.message_id || !KINDS.includes(body.kind))
        return problem(422, 'validation_error', 'Chọn câu trả lời sai ở đâu.');
      const report: AnswerReport = {
        ...body,
        id: crypto.randomUUID(),
        status: 'under_review',
        proposal_id: body.send_to_steward ? crypto.randomUUID() : null,
        created_at: new Date().toISOString(),
      };
      reports.push(report);
      return HttpResponse.json(report, { status: 201 });
    }),
  ];
}
