import { useRef, useEffect } from "react";
import { useChatStore } from "../stores/chatStore";

export function WaveformViz() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stage = useChatStore((s) => s.stage);
  const animRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const draw = () => {
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      if (stage === "idle") {
        // Subtle idle line
        ctx.strokeStyle = "rgba(100, 116, 139, 0.3)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, h / 2);
        ctx.lineTo(w, h / 2);
        ctx.stroke();
      } else if (stage === "listening") {
        // Animated waveform
        const time = Date.now() / 200;
        ctx.strokeStyle = "rgba(239, 68, 68, 0.6)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (let x = 0; x < w; x++) {
          const y =
            h / 2 +
            Math.sin(x * 0.03 + time) * 8 +
            Math.sin(x * 0.07 + time * 1.5) * 4;
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      } else if (stage === "thinking" || stage === "transcribing" || stage === "retrieving") {
        // Bouncing dots
        const time = Date.now() / 300;
        ctx.fillStyle = "rgba(59, 130, 246, 0.6)";
        for (let i = 0; i < 5; i++) {
          const x = w * (0.3 + i * 0.1);
          const y = h / 2 + Math.sin(time + i * 0.8) * 6;
          ctx.beginPath();
          ctx.arc(x, y, 3, 0, Math.PI * 2);
          ctx.fill();
        }
      } else if (stage === "speaking") {
        // Playback waveform
        const time = Date.now() / 150;
        ctx.strokeStyle = "rgba(34, 197, 94, 0.6)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (let x = 0; x < w; x++) {
          const y =
            h / 2 +
            Math.sin(x * 0.05 + time) * 6 +
            Math.sin(x * 0.02 - time * 0.5) * 3;
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }

      animRef.current = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(animRef.current);
  }, [stage]);

  return (
    <canvas
      ref={canvasRef}
      width={400}
      height={40}
      className="w-full h-10 opacity-80"
    />
  );
}
