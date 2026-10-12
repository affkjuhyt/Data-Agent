/** The Data Space the user works in, as the top bar shows it. */
export interface DataSpaceRef {
  id: string;
  name: string;
}

export interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}
