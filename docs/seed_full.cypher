// WARNING: This script clears the entire Neo4j database before seeding fresh data.
// Review before running in a shared or production environment.

// --- Purge existing graph ------------------------------------------------------
MATCH (n)
DETACH DELETE n;

// --- Recreate supporting constraints ------------------------------------------
CREATE CONSTRAINT position_name_unique IF NOT EXISTS
FOR (p:Position)
REQUIRE p.name IS UNIQUE;

CREATE CONSTRAINT tool_name_unique IF NOT EXISTS
FOR (t:Tool)
REQUIRE t.name IS UNIQUE;

CREATE CONSTRAINT toolstand_name_unique IF NOT EXISTS
FOR (s:ToolStand)
REQUIRE s.name IS UNIQUE;

CREATE CONSTRAINT routine_name_unique IF NOT EXISTS
FOR (r:Routine)
REQUIRE r.name IS UNIQUE;

// --- Seed positions with metadata ---------------------------------------------
WITH 1 AS _
UNWIND [
  {
    name: 'Home',
    role: 'home',
    description: 'Default idle pose for the robot; central safe location to begin or end workflows.'
  },
  {
    name: 'Tool_Camera_Safe_Position',
    role: 'safe_approach',
    description: 'Safe waypoint before entering the camera tool stand.'
  },
  {
    name: 'Tool_Camera_Position',
    role: 'tool_mount',
    description: 'Camera tool stand docking pose.'
  },
  {
    name: 'Tool_Weld_Safe_Position',
    role: 'safe_approach',
    description: 'Safe waypoint before entering the weld tool stand.'
  },
  {
    name: 'Tool_Weld_Position',
    role: 'tool_mount',
    description: 'Welder tool stand docking pose.'
  },
  {
    name: 'Safe_Pos_1',
    role: 'safe_approach',
    description: 'Safe waypoint leading into work position Pos_1.'
  },
  {
    name: 'Safe_Pos_2',
    role: 'safe_approach',
    description: 'Safe waypoint leading into work position Pos_2.'
  },
  {
    name: 'Safe_Pos_3',
    role: 'safe_approach',
    description: 'Safe waypoint leading into work position Pos_3.'
  },
  {
    name: 'Pos_1',
    role: 'work',
    description: 'Work location 1; accessed through Safe_Pos_1.'
  },
  {
    name: 'Pos_2',
    role: 'work',
    description: 'Work location 2; accessed through Safe_Pos_2.'
  },
  {
    name: 'Pos_3',
    role: 'work',
    description: 'Work location 3; accessed through Safe_Pos_3.'
  }
] AS nodeData
MERGE (p:Position {name: nodeData.name})
SET
  p.role = nodeData.role,
  p.description = nodeData.description,
  p.embedding = NULL,
  p.preferred_tool = NULL;

WITH 1 AS _

// --- Seed motion whitelist relationships --------------------------------------
UNWIND [
  ['Home','Safe_Pos_1'],
  ['Home','Safe_Pos_2'],
  ['Home','Safe_Pos_3'],
  ['Home','Tool_Camera_Safe_Position'],
  ['Home','Tool_Weld_Safe_Position'],
  ['Safe_Pos_1','Pos_1'],
  ['Safe_Pos_2','Pos_2'],
  ['Safe_Pos_3','Pos_3'],
  ['Tool_Camera_Safe_Position','Tool_Camera_Position'],
  ['Tool_Weld_Safe_Position','Tool_Weld_Position']
] AS pair
MATCH (a:Position {name: pair[0]}), (b:Position {name: pair[1]})
MERGE (a)-[:ONLY_ALLOWED_MOVE_TO]->(b)
MERGE (b)-[:ONLY_ALLOWED_MOVE_TO]->(a);

// --- Seed tool stands, tools, and locations ------------------------------------
UNWIND [
  {
    tool: {name: 'Camera', type: 'sensor', description: 'RGB inspection camera mounted on the wrist'},
    stand: {name: 'ToolStand_Camera', pose: 'Camera stand'},
    stand_position: 'Tool_Camera_Position'
  },
  {
    tool: {name: 'Welder', type: 'end_effector', description: 'TIG welding head'},
    stand: {name: 'ToolStand_Welder', pose: 'Welder stand'},
    stand_position: 'Tool_Weld_Position'
  }
] AS data
MERGE (t:Tool {name: data.tool.name})
  ON CREATE SET t.type = data.tool.type,
                t.description = data.tool.description
  ON MATCH SET t.type = data.tool.type,
               t.description = data.tool.description
MERGE (s:ToolStand {name: data.stand.name})
  ON CREATE SET s.pose = data.stand.pose
  ON MATCH SET s.pose = data.stand.pose
MERGE (pos:Position {name: data.stand_position})
MERGE (s)-[:LOCATED_AT]->(pos)
MERGE (t)-[:TOOL_AVAILABLE_AT]->(s);

WITH 1 AS _

// --- Seed generic tool-change routines ----------------------------------------
UNWIND [
  {
    name: 'tool_attach',
    description: 'Attach a tool from the stand slot to the robot wrist.',
    target_position: NULL,
    required_tool: NULL
  },
  {
    name: 'tool_release',
    description: 'Return the currently attached tool to the appropriate stand slot.',
    target_position: NULL,
    required_tool: NULL
  }
] AS routineData
MERGE (r:Routine {name: routineData.name})
SET r.description = routineData.description,
    r.target_position = routineData.target_position,
    r.required_tool = routineData.required_tool;

WITH 1 AS _

// --- Seed task-specific routines ----------------------------------------------
UNWIND [
  {
    name: 'camera_inspection',
    description: 'Capture RGB images for quality inspection at designated work positions.',
    required_tool: 'Camera'
  },
  {
    name: 'tack_weld',
    description: 'Perform a tack weld at the specified work location.',
    required_tool: 'Welder'
  }
] AS routineSpec
MERGE (r:Routine {name: routineSpec.name})
SET r.description = routineSpec.description,
    r.required_tool = routineSpec.required_tool

WITH 1 AS _

// --- Attach routine metadata per supported position ----------------------------
UNWIND [
  {
    routine: 'camera_inspection',
    position: 'Pos_1',
    stabilize: 2,
    action_after: 'capture_image',
    verify: 'image_ready'
  },
  {
    routine: 'camera_inspection',
    position: 'Pos_2',
    stabilize: 2,
    action_after: 'capture_image',
    verify: 'image_ready'
  },
  {
    routine: 'camera_inspection',
    position: 'Pos_3',
    stabilize: 2,
    action_after: 'capture_image',
    verify: 'image_ready'
  },
  {
    routine: 'tack_weld',
    position: 'Pos_1',
    stabilize: 1.5,
    verify: 'weld_quality_check'
  },
  {
    routine: 'tack_weld',
    position: 'Pos_2',
    stabilize: 1.5,
    verify: 'weld_quality_check'
  },
  {
    routine: 'tack_weld',
    position: 'Pos_3',
    stabilize: 1.5,
    verify: 'weld_quality_check'
  },
  {
    routine: 'tool_attach',
    position: 'Tool_Weld_Position',
    stabilize: 1.5,
    action_after: 'attach_tool',
    verify: 'Welder'
  },
  {
    routine: 'tool_attach',
    position: 'Tool_Camera_Position',
    stabilize: 1.5,
    action_after: 'attach_tool',
    verify: 'Camera'
  },
  {
    routine: 'tool_release',
    position: 'Tool_Weld_Position',
    stabilize: 1.0,
    action_after: 'detach_tool',
    verify: 'Welder'
  },
  {
    routine: 'tool_release',
    position: 'Tool_Camera_Position',
    stabilize: 1.0,
    action_after: 'detach_tool',
    verify: 'Camera'
  }
] AS supportSpec
MATCH (r:Routine {name: supportSpec.routine})
MATCH (p:Position {name: supportSpec.position})
MERGE (r)-[rel:SUPPORTED_AT]->(p)
SET rel.stabilize = supportSpec.stabilize,
    rel.action_after = supportSpec.action_after,
    rel.verify = supportSpec.verify

WITH 1 AS _

// --- Optional metadata linking positions to preferred tools -------------------
UNWIND [
  {position: 'Pos_1', preferred_tool: 'Camera'},
  {position: 'Pos_2', preferred_tool: 'Welder'}
] AS meta
MATCH (p:Position {name: meta.position})
SET p.preferred_tool = meta.preferred_tool

RETURN 'seed_complete' AS status;
