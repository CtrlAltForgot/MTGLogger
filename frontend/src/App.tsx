import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { AllInbox, CameraAlt, DarkMode, Dashboard as DashboardIcon, Download, FactCheck, Inventory2, LightMode, ShowChart, Storage, Style } from '@mui/icons-material'
import { AppBar, Box, Button, CircularProgress, CssBaseline, IconButton, Tab, Tabs, ThemeProvider, Toolbar, Tooltip, Typography } from '@mui/material'
import { appTheme } from './theme'

const Collection=lazy(()=>import('./pages/Collection'))
const Value=lazy(()=>import('./pages/Value'))
const Dashboard=lazy(()=>import('./pages/Dashboard'))
const Decks=lazy(()=>import('./pages/Decks'))
const ReviewQueue=lazy(()=>import('./pages/ReviewQueue'))
const Scanner=lazy(()=>import('./pages/Scanner'))
const Sealed=lazy(()=>import('./pages/Sealed'))
const Database=lazy(()=>import('./pages/Database'))

const pages=[
  {name:'Dashboard',icon:<DashboardIcon/>,content:<Dashboard/>},
  {name:'Scanner',icon:<CameraAlt/>,content:<Scanner/>},
  {name:'Collection',icon:<Inventory2/>,content:<Collection/>},
  {name:'Value',icon:<ShowChart/>,content:<Value/>},
  {name:'Database',icon:<Storage/>,content:<Database/>},
  {name:'Decks',icon:<Style/>,content:<Decks/>},
  {name:'Review',icon:<FactCheck/>,content:<ReviewQueue/>},
  {name:'Sealed',icon:<AllInbox/>,content:<Sealed/>},
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
  return <ThemeProvider theme={theme}><CssBaseline/>
    <AppBar position="sticky"><Toolbar sx={{minHeight:{xs:58,md:66},px:{xs:2,md:4}}}>
      <Box component="img" src="/mtglogger-mark.svg" alt="" sx={{width:42,height:42,mr:1.25,filter:'drop-shadow(0 7px 14px rgba(190,35,54,.25))'}}/>
      <Box flex={1}><Typography variant="h6" lineHeight={1}>MTGLogger</Typography><Typography variant="caption" color="text.secondary">Log your TCG collection</Typography></Box>
      {installPrompt&&<Button startIcon={<Download/>} onClick={()=>void install()} sx={{mr:1,display:{xs:'none',sm:'inline-flex'}}}>Install app</Button>}
      <Tooltip title={dark?'Use light appearance':'Use dark appearance'}><IconButton onClick={toggleTheme} sx={{border:'1px solid',borderColor:'divider',bgcolor:'action.hover'}}>{dark?<LightMode/>:<DarkMode/>}</IconButton></Tooltip>
    </Toolbar>
    <Box sx={{px:{xs:1,md:4},display:'flex',justifyContent:{md:'center'}}}><Tabs value={page} onChange={(_,value)=>changePage(value)} variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile>{pages.map(item=><Tab key={item.name} icon={item.icon} iconPosition="start" label={item.name}/>)}</Tabs></Box>
    </AppBar>
    <Box component="main" sx={{maxWidth:1500,mx:'auto',px:{xs:2,sm:3,lg:4},py:{xs:3,md:4},minHeight:'calc(100vh - 112px)'}}><Suspense fallback={<Box minHeight="50vh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>}>{pages[page].content}</Suspense></Box>
  </ThemeProvider>
}
