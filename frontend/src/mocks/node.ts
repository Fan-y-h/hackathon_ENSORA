// Same fake backend as in the browser, but for tests running in Node.
import { setupServer } from "msw/node";

import { handlers } from "./handlers";

export const mockServer = setupServer(...handlers);
