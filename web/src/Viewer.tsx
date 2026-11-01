import { memo, useEffect, useRef, useState } from "react";
import type { GLViewer } from "3dmol";
import type { Candidate } from "./api";
import { artifactUrl } from "./api";

export const Viewer = memo(
  function Viewer({
    candidate,
    initial,
    overlay,
    showClashes,
    synthetic,
  }: {
    candidate: Candidate | undefined;
    initial: Candidate | undefined;
    overlay: boolean;
    showClashes: boolean;
    synthetic: boolean;
  }) {
    const container = useRef<HTMLDivElement>(null);
    const viewer = useRef<GLViewer | null>(null);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    useEffect(() => {
      if (!candidate || !container.current) return;
      let disposed = false;
      const controller = new AbortController();
      setError("");
      setLoading(true);
      const get = async (id: string) => {
        const r = await fetch(artifactUrl(id), { signal: controller.signal });
        if (!r.ok) throw new Error("Structure artifact unavailable");
        return r.text();
      };
      async function load() {
        try {
          const [lib, protein, ligand, original] = await Promise.all([
            import("3dmol"),
            get(candidate!.artifact_ids.protein),
            get(candidate!.artifact_ids.ligand),
            overlay && initial && initial.id !== candidate!.id
              ? get(initial.artifact_ids.ligand)
              : Promise.resolve(null),
          ]);
          if (disposed || !container.current) return;
          viewer.current?.clear();
          container.current.replaceChildren();
          const v = lib.createViewer(container.current, {
            backgroundColor: "#eef3ef",
            antialias: true,
          });
          viewer.current = v;
          const p = v.addModel(protein, "pdb");
          if (synthetic) {
            p.setStyle(
              {},
              { sphere: { radius: 0.35, color: "#a7bcb0", opacity: 0.65 } },
            );
            const atoms = p.selectedAtoms({});
            for (let i = 1; i < atoms.length; i++)
              v.addCylinder({
                start: {
                  x: atoms[i - 1].x!,
                  y: atoms[i - 1].y!,
                  z: atoms[i - 1].z!,
                },
                end: { x: atoms[i].x!, y: atoms[i].y!, z: atoms[i].z! },
                radius: 0.1,
                color: "#a7bcb0",
                opacity: 0.5,
              });
          } else
            p.setStyle({}, { cartoon: { color: "#afc4b4", opacity: 0.75 } });
          const l = v.addModel(ligand, "sdf");
          l.setStyle(
            {},
            {
              stick: { radius: 0.2, colorscheme: "greenCarbon" },
              sphere: { scale: 0.22 },
            },
          );
          if (original) {
            // Apply server-calculated alignment if available; never fit ligands.
            const originalModel = v.addModel(original, "sdf");
            const transform = candidate!.provenance.initial_alignment as
              { rotation?: number[][]; translation?: number[] } | undefined;
            if (transform?.rotation && transform.translation) {
              const r = transform.rotation,
                t = transform.translation;
              for (const atom of originalModel.selectedAtoms({})) {
                const p = [atom.x!, atom.y!, atom.z!];
                [atom.x, atom.y, atom.z] = [0, 1, 2].map((j) =>
                  p.reduce((sum, x, k) => sum + x * r[k][j], t[j]),
                );
              }
            }
            originalModel.setStyle(
              {},
              { stick: { radius: 0.12, color: "#df9b65", opacity: 0.5 } },
            );
          }
          if (showClashes)
            for (const contact of candidate!.diagnostics.closest_contacts ??
              []) {
              const a = contact.ligand_xyz as number[],
                b = contact.protein_xyz as number[];
              if (a && b)
                v.addCylinder({
                  start: { x: a[0], y: a[1], z: a[2] },
                  end: { x: b[0], y: b[1], z: b[2] },
                  radius: 0.055,
                  color: "#c95e4e",
                  dashed: true,
                });
            }
          v.zoomTo({ model: l.getID() });
          v.zoom(0.72);
          v.render();
        } catch (e) {
          if (!disposed)
            setError(
              e instanceof Error ? e.message : "WebGL viewer unavailable",
            );
        } finally {
          if (!disposed) setLoading(false);
        }
      }
      void load();
      const observer = new ResizeObserver(() => {
        viewer.current?.resize();
        viewer.current?.render();
      });
      observer.observe(container.current);
      return () => {
        disposed = true;
        controller.abort();
        observer.disconnect();
        viewer.current?.clear();
        viewer.current = null;
      };
    }, [candidate, initial, overlay, showClashes, synthetic]);
    return (
      <div className="molecule-shell">
        <div
          ref={container}
          className="molecule"
          aria-label="Interactive protein and ligand structure"
        />
        {!candidate ? (
          <div className="viewer-empty">
            <span className="molecule-symbol">⌬</span>
            <strong>Your next investigation starts here</strong>
            <span>Run the synthetic example or submit a complex.</span>
          </div>
        ) : null}
        {loading ? (
          <div className="viewer-message">Loading structure…</div>
        ) : null}
        {error ? (
          <div className="viewer-message error" role="alert">
            {error}. Structure files remain available in Results.
          </div>
        ) : null}
        <div className="viewer-caption">
          {synthetic ? "ARTIFICIAL Cα SCAFFOLD" : "PROTEIN–LIGAND COMPLEX"}
          <span>Drag to rotate · scroll to zoom</span>
        </div>
      </div>
    );
  },
  (a, b) =>
    a.candidate?.id === b.candidate?.id &&
    a.initial?.id === b.initial?.id &&
    a.overlay === b.overlay &&
    a.showClashes === b.showClashes &&
    a.synthetic === b.synthetic,
);
