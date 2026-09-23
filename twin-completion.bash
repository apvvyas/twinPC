# bash tab completion for `twin` (sourced from ~/.bashrc)
_twin() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    local cmds="route viewer run ls logs attach send kill claude cc bizdev gpu audio clip shelf top ps pkill push pull models sh desktop kvm unlock-screen gui wake unlock off reboot status link help"
    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=($(compgen -W "$cmds" -- "$cur"))
    elif [[ $COMP_CWORD -eq 2 ]]; then
        case ${COMP_WORDS[1]} in
            logs|attach|send|kill)
                # complete job names from the twin's tmux sessions
                local jobs
                jobs=$(ssh -o ConnectTimeout=2 -o BatchMode=yes twin 'tmux ls -F "#S" 2>/dev/null' 2>/dev/null)
                COMPREPLY=($(compgen -W "$jobs" -- "$cur")) ;;
            clip) COMPREPLY=($(compgen -W "status on off" -- "$cur")) ;;
            shelf) COMPREPLY=($(compgen -W "status on off" -- "$cur")) ;;
            push) COMPREPLY=($(compgen -f -- "$cur")) ;;
        esac
    fi
}
complete -F _twin twin
