import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { AutoStories, CollectionsBookmark, DarkMode, Download, Inventory2, LightMode, MenuBook, Paid, Search, Visibility } from '@mui/icons-material'
import { AppBar, Box, Button, CircularProgress, CssBaseline, IconButton, Tab, Tabs, ThemeProvider, Toolbar, Tooltip, Typography } from '@mui/material'
import { appTheme } from './theme'
import { CardDetailsProvider } from './components/CardDetails'

const Collection=lazy(()=>import('./pages/Collection'))
const Value=lazy(()=>import('./pages/Value'))
const Dashboard=lazy(()=>import('./pages/Dashboard'))
const Decks=lazy(()=>import('./pages/Decks'))
const ReviewQueue=lazy(()=>import('./pages/ReviewQueue'))
const Scanner=lazy(()=>import('./pages/Scanner'))
const Database=lazy(()=>import('./pages/Database'))

const pages=[
  {name:'Dashboard',icon:<MenuBook/>,content:<Dashboard/>},
  {name:'Scanner',icon:<Visibility/>,content:<Scanner/>},
  {name:'Collection',icon:<CollectionsBookmark/>,content:<Collection/>},
  {name:'Value',icon:<Paid/>,content:<Value/>},
  {name:'Database',icon:<AutoStories/>,content:<Database/>},
  {name:'Decks',icon:<Inventory2/>,content:<Decks/>},
  {name:'Review',icon:<Search/>,content:<ReviewQueue/>},
]

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{outcome:'accepted'|'dismissed'}>
}

export default function App(){
  const requestedPage=new URLSearchParams(location.search).get('page')
  const requestedIndex=pages.findIndex(item=>item.name.toLowerCase()===requestedPage)
  const [page,setPage]=useState(requestedIndex>=0?requestedIndex:1)
  const [dark,setDark]=useState(()=>localStorage.getItem('mtglogger-theme')!=='light')
  const [installPrompt,setInstallPrompt]=useState<InstallPromptEvent>()
  const theme=useMemo(()=>appTheme(dark),[dark])
  useEffect(()=>{
    const capture=(event:Event)=>{event.preventDefault();setInstallPrompt(event as InstallPromptEvent)}
    const installed=()=>setInstallPrompt(undefined)
    window.addEventListener('beforeinstallprompt',capture)
    window.addEventListener('appinstalled',installed)
    return ()=>{window.removeEventListener('beforeinstallprompt',capture);window.removeEventListener('appinstalled',installed)}
  },[])
  const toggleTheme=()=>setDark(value=>{localStorage.setItem('mtglogger-theme',value?'light':'dark');return !value})
  const changePage=(value:number)=>{setPage(value);history.replaceState(null,'',`?page=${pages[value].name.toLowerCase()}`)}
  const install=async()=>{if(!installPrompt)return;await installPrompt.prompt();await installPrompt.userChoice;setInstallPrompt(undefined)}
  return <ThemeProvider theme={theme}><CssBaseline/><CardDetailsProvider>
    <AppBar position="sticky"><Toolbar sx={{minHeight:{xs:48,md:52},px:{xs:1.5,md:2}}}>
      <Box component="img" src="/mtglogger-card-stack.png" alt="" sx={{width:38,height:38,objectFit:'contain',mr:.9,filter:'drop-shadow(0 8px 18px rgba(190,35,54,.3))'}}/>
      <Box flex={1}><Typography variant="h6" lineHeight={1}>MTGLogger</Typography><Typography variant="caption" color="text.secondary">Log your TCG collection</Typography></Box>
      {installPrompt&&<Button startIcon={<Download/>} onClick={()=>void install()} sx={{mr:1,display:{xs:'none',sm:'inline-flex'}}}>Install app</Button>}
      <Tooltip title={dark?'Use light appearance':'Use dark appearance'}><IconButton onClick={toggleTheme} sx={{border:'1px solid',borderColor:'divider',bgcolor:'action.hover'}}>{dark?<LightMode/>:<DarkMode/>}</IconButton></Tooltip>
    </Toolbar>
    <Box sx={{px:{xs:1,md:4},display:'flex',justifyContent:{md:'center'}}}><Tabs value={page} onChange={(_,value)=>changePage(value)} variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile sx={{minHeight:46,'& .MuiTab-root':{minHeight:46,fontSize:'.92rem',px:{xs:1.25,md:2}},'& .MuiSvgIcon-root':{fontSize:23}}}>{pages.map(item=><Tab key={item.name} icon={item.icon} iconPosition="start" label={item.name}/>)}</Tabs></Box>
    </AppBar>
    <Box component="main" sx={{maxWidth:1580,mx:'auto',px:{xs:2,sm:3,lg:3.5},py:{xs:3,md:3.5},minHeight:'calc(100vh - 108px)'}}><Suspense fallback={<Box minHeight="50vh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>}>{pages[page].content}</Suspense></Box>
  </CardDetailsProvider></ThemeProvider>
}
