import * as THREE from "three";

interface EdgeMeshState {
  mesh: THREE.Mesh;
  material: THREE.Material | THREE.Material[];
  renderOrder: number;
  visible: boolean;
  replacement: THREE.MeshBasicMaterial;
}

export interface VisibleEdgePass {
  group: THREE.Group;
  meshes: EdgeMeshState[];
  restore: () => void;
}

/**
 * Prepare one ordinary depth-tested scene render: opaque white meshes write
 * depth and their black edge siblings are tested against that same depth.
 * Nothing is hidden and no renderer clear-state is carried across frames.
 */
export function prepareVisibleEdgePass(scene: THREE.Scene): VisibleEdgePass {
  const group = new THREE.Group();
  group.name = "whole-home-visible-edge-pass";
  const meshes: EdgeMeshState[] = [];
  const lines: THREE.LineSegments[] = [];
  scene.updateMatrixWorld(true);
  scene.traverse((object) => {
    if (!(object instanceof THREE.Mesh) || !object.visible || object.parent === group) return;
    const replacement = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      side: THREE.DoubleSide,
      depthTest: true,
      depthWrite: true,
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    });
    meshes.push({
      mesh: object,
      material: object.material,
      renderOrder: object.renderOrder,
      visible: object.visible,
      replacement,
    });
    object.material = replacement;
    object.renderOrder = 0;

    const line = new THREE.LineSegments(
      new THREE.EdgesGeometry(object.geometry, 25),
      new THREE.LineBasicMaterial({
        color: 0x111111,
        depthTest: true,
        depthWrite: false,
        depthFunc: THREE.LessEqualDepth,
      }),
    );
    line.matrixAutoUpdate = false;
    line.matrix.copy(object.matrixWorld);
    line.renderOrder = 10;
    group.add(line);
    lines.push(line);
  });
  scene.add(group);

  let restored = false;
  return {
    group,
    meshes,
    restore() {
      if (restored) return;
      restored = true;
      scene.remove(group);
      for (const state of meshes) {
        state.mesh.material = state.material;
        state.mesh.renderOrder = state.renderOrder;
        state.mesh.visible = state.visible;
        state.replacement.dispose();
      }
      for (const line of lines) {
        line.geometry.dispose();
        const materials = Array.isArray(line.material) ? line.material : [line.material];
        materials.forEach((material) => material.dispose());
      }
      group.clear();
    },
  };
}

