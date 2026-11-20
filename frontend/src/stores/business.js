import {batches,mappings,runs,billing} from '../api/business.js';
export const store={batches:[],mappings:[],runs:[],billing:null,user:null};
export async function refresh(){[store.batches,store.mappings,store.runs,store.billing]=await Promise.all([batches.list(),mappings.list(),runs.list(),billing.list()]);}
