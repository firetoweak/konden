import { z } from "zod";

export type TraceScope = { sessionId: string; branch: string[] };

export function traceParams(branch: string[], entry?: string) {
  const params = new URLSearchParams();
  for (const commandId of branch) params.append("branch", commandId);
  if (entry !== undefined) params.set("entry", entry);
  return params;
}

const traceKind = z.enum(["input", "model", "tool", "user", "catalog", "compact", "subagent"]);
const callStatus = z.enum(["passed", "failed", "unknown", "pending", "rejected", "not_applicable"]);
const usage = z.object({
  input_tokens: z.number().optional(), output_tokens: z.number().optional(),
  cached_input_tokens: z.number().optional(), cache_write_input_tokens: z.number().optional(),
});
const entry = z.object({
  id: z.string(), kind: traceKind, sequence: z.number(), at: z.number(), start: z.number(), title: z.string(),
  end: z.number().nullable().optional(), step: z.number().optional(), user: z.boolean().optional(),
  on_axis: z.boolean().optional(), usage: usage.optional(), status: callStatus.optional(),
  preview: z.string().optional(), result: z.string().optional(), branch: z.string().optional(),
});
export type TraceEntry = z.infer<typeof entry>;

export const traceIndexSchema = z.object({
  session_id: z.string(), revision: z.number(), task: z.string().nullable(), entries: entry.array(),
});

export const traceMessageSchema = z.object({ role: z.string(), content: z.unknown() }).loose();
const request = z.object({ model: z.string(), messages: traceMessageSchema.array(), tools: z.unknown().array().nullable() });
const toolset = z.object({ id: z.string(), description: z.string() });
export const traceDetailSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("input"), request: request.nullable(),
    triggers: z.object({ sequence: z.number(), kind: z.string(), text: z.string().nullable() }).array() }),
  z.object({ kind: z.literal("model"), reasoning: z.string().nullable(),
    output_source: z.enum(["recorded", "committed"]),
    output: z.object({ content: z.string(), calls: z.object({
      id: z.string().optional(), name: z.string(), arguments: z.union([z.string(), z.record(z.string(), z.json())]),
    }).array() }),
  }),
  z.object({ kind: z.literal("tool"), arguments: z.record(z.string(), z.unknown()),
    outcome: z.object({ status: z.string(), value: z.unknown(), error_type: z.string().nullable(),
      error_message: z.string().nullable() }).nullable(),
    issued_at: z.number(), dispatched_at: z.number().nullable(), received_at: z.number().nullable(),
    attempts: z.object({ number: z.number(), at: z.number(), phase: z.enum(["unknown", "orphaned", "terminal"]) }).array() }),
  z.object({ kind: z.literal("user"), text: z.string(), images: z.string().array() }),
  z.object({ kind: z.literal("catalog"), toolsets: toolset.array(), tools: z.unknown().array() }),
  z.object({ kind: z.literal("compact"), published: z.boolean(), started_at: z.number().nullable(),
    at: z.number(), upto: z.number(), summary: z.string().nullable() }),
]);

export type TraceIndex = z.infer<typeof traceIndexSchema>;
export type TraceDetail = z.infer<typeof traceDetailSchema>;
export type TraceMessage = z.infer<typeof traceMessageSchema>;
